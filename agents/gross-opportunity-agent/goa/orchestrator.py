"""
GOA Orchestrator — root ADK agent coordinating the six-function pipeline.
Runs sources in parallel, each record through: adapter → normalize → dedup → gate → store.
Section 6.1 of the build spec.
"""

from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any

from . import adapters as adapter_registry
from .normalize.normalizer import normalize
from .dedup.idempotency import stable_hash, commit_record
from .dedup.match import blocking_candidates, score_candidates, partition_by_confidence
from .dedup.merge import merge_into, model_arbitrate
from .dedup.embed import is_same_project
from .gate.rules import evaluate as gate_evaluate
from .gate.classifier import evaluate_with_classifier
from .stores import cloudsql, bigquery, firestore

log = logging.getLogger(__name__)


async def _get_blocking_candidates_fn(city: str | None, bid_date: Any) -> list[dict]:
    """Adapter: pulls blocking candidates from Cloud SQL."""
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM opportunity WHERE city = $1 AND bid_date = $2 AND status = 'active'",
            city, bid_date,
        )
    return [dict(r) for r in rows]


async def run_source(source_id: str, mode: str) -> None:
    """Process one source. Called by the backfill or delta job."""
    run_id = uuid.uuid4().hex[:8]
    log.info("Orchestrator: run_source source=%s mode=%s run=%s", source_id, mode, run_id)

    async def act(step, message, **kw):
        await cloudsql.log_activity(step, message, run_id=run_id, source_id=source_id, **kw)

    await act("run", f"Started {mode} run for {source_id}", level="info")
    await act("run", "Orchestrator online — resolving source config, rules, scope, and today's request budget")

    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        source_row = await conn.fetchrow("SELECT * FROM source_registry WHERE source_id = $1", source_id)
    if source_row is None:
        log.error("Orchestrator: source_id=%s not found in source_registry", source_id)
        await act("error", f"source_id={source_id} not found in registry", level="warn")
        return
    if not source_row["enabled"]:
        log.warning("Orchestrator: source_id=%s is disabled, skipping", source_id)
        await act("error", f"source {source_id} is disabled (awaiting APPROVE)", level="warn")
        return

    import json
    stored_cfg = json.loads(source_row["config"] or "{}")
    # The adapter reads base_url/auth/pagination/field_map/query_params at the top level.
    # The source_registry stores the full source JSON in the `config` column, so flatten
    # it onto the registry row (source_id, name, method, watermark_field, rate_limit, …).
    source_cfg = dict(source_row)
    source_cfg.update(stored_cfg)
    source_cfg["source_id"] = source_id
    if isinstance(source_cfg.get("rate_limit"), str):
        source_cfg["rate_limit"] = json.loads(source_cfg["rate_limit"] or "{}")

    adapter_cls = adapter_registry.for_source(source_id) or adapter_registry.for_method(source_row["method"])
    adapter = adapter_cls(source_cfg)
    watermark = await cloudsql.get_watermark(source_id)

    rules = await cloudsql.get_rules("initial_screening")
    scope = await cloudsql.get_scope()

    import os
    from .adapters.base import RateLimited, BudgetExhausted

    # ── Daily request budget (never let the source 429 us) ─────────────────────
    # requests_per_day comes from the source config (env GOA_REQUESTS_PER_DAY wins —
    # flip it to 1000 the day the SAM.gov role upgrade lands, no redeploy needed).
    # reserve_for_ui requests are held back so reviewers can always pull full reports.
    rl = source_cfg.get("rate_limit") or {}
    env_rpd = os.environ.get("GOA_REQUESTS_PER_DAY")
    requests_per_day = int(env_rpd) if env_rpd else int(rl.get("requests_per_day") or 0)  # 0 = unlimited
    reserve = int(os.environ.get("GOA_BUDGET_RESERVE", rl.get("reserve_for_ui") or 0))
    if requests_per_day:
        used_today = await cloudsql.get_requests_used_today(source_id)
        run_budget = max(0, requests_per_day - reserve - used_today)
        await act("budget",
                  f"Request budget: {used_today}/{requests_per_day} used today (UTC) · "
                  f"{run_budget} available this run · {reserve} reserved for console full-pulls",
                  detail={"requests_per_day": requests_per_day, "used_today": used_today,
                          "run_budget": run_budget, "reserve_for_ui": reserve,
                          "override_env": bool(env_rpd)})
        if run_budget == 0:
            await act("budget", "Daily request budget exhausted — skipping run; resumes after 00:00 UTC reset",
                      level="warn")
            await act("run", f"Skipped {mode} run (no request budget left today)", level="warn",
                      detail={"processed": 0, "budget_exhausted": True})
            return
        adapter.set_request_budget(run_budget)

    max_records = int(os.environ.get("GOA_MAX_RECORDS", "0"))  # 0 = no cap
    processed = 0
    rate_limited = False
    budget_exhausted = False

    puller = adapter.pull(mode, watermark)
    while True:
        try:
            raw = next(puller)
        except StopIteration:
            break
        except BudgetExhausted as e:
            budget_exhausted = True
            log.warning("Orchestrator: request budget exhausted, stopping pull: %s", e)
            await act("budget", f"Stopped BEFORE hitting the source limit — budget spent after "
                                f"{processed} record(s); tomorrow's run resumes this window",
                      level="warn", detail={"reason": str(e), "requests_made": adapter.requests_made})
            break
        except RateLimited as e:
            rate_limited = True
            log.warning("Orchestrator: source rate-limited, stopping pull: %s", e)
            await act("error", f"Source rate-limited (429) — stopped after {processed} record(s); next run resumes",
                      level="warn", detail={"reason": str(e)})
            break
        if max_records and processed >= max_records:
            log.info("Orchestrator: hit GOA_MAX_RECORDS=%d cap, stopping pull", max_records)
            break
        processed += 1
        record_hash = stable_hash(source_id, raw)

        if mode == "delta":
            pool2 = await cloudsql.get_pool()
            async with pool2.acquire() as conn:
                already = await conn.fetchval(
                    "SELECT 1 FROM fired_marker WHERE source_id=$1 AND source_record_hash=$2",
                    source_id, record_hash,
                )
            if already:
                continue

        # Per-record pipeline trace — what each of the six functions did, for the UI.
        trace: dict = {"source_id": source_id, "mode": mode}

        # Step 1 — Normalize
        norm = normalize(raw, source_cfg)
        await bigquery.insert_raw(source_id, norm.source_links[0].source_record_id if norm.source_links else None, raw, mode)
        trace["normalize"] = {
            "project_name": norm.project_name, "record_type": norm.record_type,
            "csi_divisions": norm.csi_divisions, "identity_key": norm.project_identity_key[:16],
        }
        await act("normalize", f"Normalized “{(norm.project_name or 'unnamed')[:60]}” · {norm.record_type} · CSI {'/'.join(norm.csi_divisions) or '—'}",
                  detail=trace["normalize"])

        # Step 2 — Dedup
        existing_by_key = await _find_by_identity_key(norm.project_identity_key)
        if existing_by_key:
            norm.opportunity_id = existing_by_key["opportunity_id"]
            merged = merge_into(norm, existing_by_key)
            event_type = "deduped"
            trace["dedup"] = {"result": "merged", "method": "identity_key", "candidates": 1}
        else:
            # Blocking + fuzzy
            candidates = await blocking_candidates(norm, _get_blocking_candidates_fn)
            scored = score_candidates(norm, candidates)
            certain, ambiguous = partition_by_confidence(scored)
            if certain:
                target = certain[0]
                norm.opportunity_id = target["opportunity_id"]
                merged = merge_into(norm, target)
                event_type = "deduped"
                trace["dedup"] = {"result": "merged", "method": "fuzzy_certain", "candidates": len(candidates)}
            elif ambiguous:
                # Embedding compare, then model arbitration
                merged = norm
                event_type = "normalized"
                trace["dedup"] = {"result": "new", "method": "ambiguous_arbitrated", "candidates": len(ambiguous)}
                for cand in ambiguous:
                    same = await is_same_project(norm, cand)
                    if same or await model_arbitrate(norm, cand):
                        norm.opportunity_id = cand["opportunity_id"]
                        merged = merge_into(norm, cand)
                        event_type = "deduped"
                        trace["dedup"] = {"result": "merged", "method": "model_arbitration", "candidates": len(ambiguous)}
                        break
            else:
                merged = norm
                event_type = "normalized"
                trace["dedup"] = {"result": "new", "method": "no_candidates", "candidates": 0}

        # Step 3 — Gate
        gate_result = gate_evaluate(merged, rules, scope)
        gate_final = await evaluate_with_classifier(merged, gate_result, scope)
        merged.gate_passed = gate_final["passed"]
        merged.gate_score = gate_final["score"]
        merged.gate_matched_rules = gate_final["matched_rules"]
        trace["gate"] = {
            "passed": gate_final["passed"], "score": gate_final["score"],
            "matched_rules": gate_final["matched_rules"],
            "used_classifier": bool(gate_final.get("classifier_engine")),
            "classifier_engine": gate_final.get("classifier_engine", ""),
            "classifier_reason": gate_final.get("classifier_reason", ""),
        }
        merged.agent_trace = trace

        # Activity: dedup + gate outcomes
        dd = trace.get("dedup", {})
        if dd.get("result") == "merged":
            await act("dedup", f"Merged duplicate via {dd.get('method')} → 1 opportunity", level="good", detail=dd)
        else:
            await act("dedup", f"New project ({dd.get('method')})", detail=dd)
        g = trace["gate"]
        gmsg = f"Gate {'KEPT' if g['passed'] else 'flagged'} · score {g['score']:.2f}"
        if g.get("classifier_reason"):
            gmsg += f" — {g['classifier_reason'][:90]}"
        elif g.get("matched_rules"):
            gmsg += f" · rules {', '.join(g['matched_rules'])}"
        await act("gate", gmsg, level=("good" if g["passed"] else "drop"), detail=g)

        gate_event = "gated_kept" if merged.gate_passed else "gated_dropped"

        # Step 4 — Commit (atomic with idempotency marker)
        async def upsert(opp):
            await cloudsql.upsert_opportunity(opp)
            for sl in opp.source_links:
                await cloudsql.upsert_source_link(opp.opportunity_id, sl.source_name, sl.source_url, sl.source_record_id)
            await bigquery.upsert_gross(opp)
            return opp

        committed = await commit_record(source_id, record_hash, merged, upsert)
        if committed:
            await act("commit", f"Committed “{(merged.project_name or 'unnamed')[:50]}” to serving store",
                      level="good", opportunity_id=merged.opportunity_id)
            await firestore.log_activity(gate_event, source_id,
                f"{source_cfg.get('name', source_id)}: {merged.project_name or 'unnamed'}",
                merged.opportunity_id)

    # Persist actual API usage to the daily ledger (shared with the console's
    # on-demand full pulls, survives restarts, keys on the UTC reset day).
    if adapter.requests_made:
        total_used = await cloudsql.record_requests_used(source_id, adapter.requests_made)
        await act("budget", f"Spent {adapter.requests_made} API request(s) this run · "
                            f"{total_used}{'/' + str(requests_per_day) if requests_per_day else ''} used today",
                  detail={"run_requests": adapter.requests_made, "used_today": total_used})

    stopped_early = rate_limited or budget_exhausted
    if not stopped_early:
        await cloudsql.advance_watermark(source_id, adapter.cursor)
    outcome = "Stopped (rate-limited)" if rate_limited else ("Stopped (budget spent)" if budget_exhausted else "Completed")
    await act("run", f"{outcome} {mode} run · {processed} record(s) processed · {adapter.requests_made} API request(s)",
              level=("warn" if stopped_early else "good"),
              detail={"processed": processed, "rate_limited": rate_limited,
                      "budget_exhausted": budget_exhausted, "requests_made": adapter.requests_made})
    await firestore.log_activity("pulled", source_id, f"Completed {mode} pull for {source_cfg.get('name', source_id)}")
    log.info("Orchestrator: completed source=%s mode=%s run=%s processed=%d requests=%d",
             source_id, mode, run_id, processed, adapter.requests_made)


async def _find_by_identity_key(key: str) -> dict | None:
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM opportunity WHERE project_identity_key = $1 LIMIT 1", key)
    return dict(row) if row else None

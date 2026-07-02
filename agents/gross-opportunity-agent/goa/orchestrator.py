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
    log.info("Orchestrator: run_source source=%s mode=%s", source_id, mode)

    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        source_row = await conn.fetchrow("SELECT * FROM source_registry WHERE source_id = $1", source_id)
    if source_row is None:
        log.error("Orchestrator: source_id=%s not found in source_registry", source_id)
        return
    if not source_row["enabled"]:
        log.warning("Orchestrator: source_id=%s is disabled, skipping", source_id)
        return

    source_cfg = dict(source_row)
    import json
    source_cfg["config"] = json.loads(source_cfg.get("config") or "{}")

    adapter_cls = adapter_registry.for_source(source_id) or adapter_registry.for_method(source_row["method"])
    adapter = adapter_cls(source_cfg)
    watermark = await cloudsql.get_watermark(source_id)

    rules = await cloudsql.get_rules("initial_screening")
    scope = await cloudsql.get_scope()

    for raw in adapter.pull(mode, watermark):
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

        # Step 1 — Normalize
        norm = normalize(raw, source_cfg)
        await bigquery.insert_raw(source_id, norm.source_links[0].source_record_id if norm.source_links else None, raw, mode)

        # Step 2 — Dedup
        existing_by_key = await _find_by_identity_key(norm.project_identity_key)
        if existing_by_key:
            norm.opportunity_id = existing_by_key["opportunity_id"]
            merged = merge_into(norm, existing_by_key)
            event_type = "deduped"
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
            elif ambiguous:
                # Embedding compare, then model arbitration
                for cand in ambiguous:
                    same = await is_same_project(norm, cand)
                    if same or await model_arbitrate(norm, cand):
                        norm.opportunity_id = cand["opportunity_id"]
                        merged = merge_into(norm, cand)
                        event_type = "deduped"
                        break
                else:
                    merged = norm
                    event_type = "normalized"
            else:
                merged = norm
                event_type = "normalized"

        # Step 3 — Gate
        gate_result = gate_evaluate(merged, rules, scope)
        gate_final = await evaluate_with_classifier(merged, gate_result, scope)
        merged.gate_passed = gate_final["passed"]
        merged.gate_score = gate_final["score"]
        merged.gate_matched_rules = gate_final["matched_rules"]

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
            await firestore.log_activity(gate_event, source_id,
                f"{source_cfg.get('name', source_id)}: {merged.project_name or 'unnamed'}",
                merged.opportunity_id)

    await cloudsql.advance_watermark(source_id, adapter.cursor)
    await firestore.log_activity("pulled", source_id, f"Completed {mode} pull for {source_cfg.get('name', source_id)}")
    log.info("Orchestrator: completed source=%s mode=%s", source_id, mode)


async def _find_by_identity_key(key: str) -> dict | None:
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM opportunity WHERE project_identity_key = $1 LIMIT 1", key)
    return dict(row) if row else None

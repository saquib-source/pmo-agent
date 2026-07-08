"""
Async full-report job — fetches the full source record for one opportunity on demand.
Triggered by pull_full_report. Sets fetch_state to full_pulled or failed.
Section 9.1 of the build spec.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

from ..stores import cloudsql
from .. import adapters as adapter_registry

log = logging.getLogger(__name__)

# Simple in-process queue; replace with Cloud Tasks for production
_queue: asyncio.Queue[str] = asyncio.Queue()
_worker_task: asyncio.Task | None = None


def enqueue_full_report(opportunity_id: str) -> None:
    _queue.put_nowait(opportunity_id)
    _ensure_worker()


def _ensure_worker() -> None:
    global _worker_task
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker())


async def _worker() -> None:
    while True:
        opportunity_id = await _queue.get()
        try:
            await _pull_full_report_job(opportunity_id)
        except Exception as e:
            log.error("Full report worker error for %s: %s", opportunity_id, e)
        finally:
            _queue.task_done()


async def _load_source_configs() -> dict[str, dict]:
    """source_registry rows flattened the same way the orchestrator flattens them,
    keyed by BOTH source_id and display name (source_link stores the display name)."""
    import json
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM source_registry")
    out: dict[str, dict] = {}
    for row in rows:
        cfg = dict(row)
        cfg.update(json.loads(row["config"] or "{}"))
        cfg["source_id"] = row["source_id"]
        if isinstance(cfg.get("rate_limit"), str):
            cfg["rate_limit"] = json.loads(cfg["rate_limit"] or "{}")
        out[row["source_id"]] = cfg
        out[row["name"]] = cfg
    return out


async def _pull_full_report_job(opportunity_id: str) -> None:
    import os
    opp = await cloudsql.get_opportunity(opportunity_id)
    if opp is None:
        log.warning("pull_full_report: opportunity %s not found", opportunity_id)
        return

    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM source_link WHERE opportunity_id = $1", opportunity_id)
    source_links = [dict(r) for r in rows]
    configs = await _load_source_configs()

    try:
        full_record: dict = {}
        all_variants: list[dict] = []
        fetched_any = False
        for link in source_links:
            cfg = configs.get(link["source_name"])
            if cfg is None:
                log.debug("pull_full_report: no source config for %s", link["source_name"])
                continue
            source_id = cfg["source_id"]
            adapter_cls = adapter_registry.for_source(source_id) or adapter_registry.for_method(cfg.get("method", "rest"))
            adapter = adapter_cls(cfg)

            # On-demand pulls draw on the SAME daily ledger as scheduled runs (this is
            # what reserve_for_ui holds space for). Never exceed the source's day quota.
            rl = cfg.get("rate_limit") or {}
            env_rpd = os.environ.get("GOA_REQUESTS_PER_DAY")
            requests_per_day = int(env_rpd) if env_rpd else int(rl.get("requests_per_day") or 0)
            if requests_per_day:
                used = await cloudsql.get_requests_used_today(source_id)
                remaining = max(0, requests_per_day - used)
                if remaining == 0:
                    await cloudsql.log_activity(
                        "fetch_full", f"Full-report pull blocked — daily request budget spent "
                                      f"({used}/{requests_per_day}); resets 00:00 UTC",
                        source_id=source_id, opportunity_id=opportunity_id, level="warn")
                    raise RuntimeError(f"daily request budget spent ({used}/{requests_per_day})")
                adapter.set_request_budget(remaining)

            try:
                full, variants = adapter.fetch_full(link.get("source_record_id", ""))
                fetched_any = True
                full_record.update(full)
                all_variants.extend([{**v, "source_name": link["source_name"]} for v in variants])
                await cloudsql.log_activity(
                    "fetch_full", f"Pulled full RFP report from {link['source_name']} "
                                  f"({len(variants)} attachment(s))",
                    source_id=source_id, opportunity_id=opportunity_id, level="good",
                    detail={"attachments": len(variants)})
            except NotImplementedError:
                log.debug("pull_full_report: fetch_full not supported by %s", link["source_name"])
            finally:
                if adapter.requests_made:
                    await cloudsql.record_requests_used(source_id, adapter.requests_made)

        if not fetched_any:
            # No source on this record supports on-demand retrieval (e.g. emailed
            # leads — SR-1 pending). Say so honestly instead of claiming success
            # with an empty report.
            msg = ("None of this record's sources support on-demand full-record "
                   "retrieval yet — pending specification SR-1 (mixed-source full reports)")
            await cloudsql.log_activity(
                "fetch_full", msg, opportunity_id=opportunity_id, level="warn")
            raise RuntimeError(msg)

        await cloudsql.merge_full_record(opportunity_id, full_record)
        await cloudsql.add_spec_variants(opportunity_id, all_variants)
        await cloudsql.set_fetch_state(opportunity_id, "full_pulled")
        log.info("pull_full_report: full_pulled for %s", opportunity_id)

    except Exception as e:
        await cloudsql.set_fetch_error(opportunity_id, {
            "code": type(e).__name__,
            "message": str(e),
            "failed_at": datetime.now(timezone.utc).isoformat(),
        })
        log.warning("pull_full_report: failed for %s: %s", opportunity_id, e)

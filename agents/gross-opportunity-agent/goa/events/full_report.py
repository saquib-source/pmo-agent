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


async def _pull_full_report_job(opportunity_id: str) -> None:
    opp = await cloudsql.get_opportunity(opportunity_id)
    if opp is None:
        log.warning("pull_full_report: opportunity %s not found", opportunity_id)
        return

    import json
    source_links = []
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM source_link WHERE opportunity_id = $1", opportunity_id)
    source_links = [dict(r) for r in rows]

    try:
        full_record: dict = {}
        all_variants: list[dict] = []
        for link in source_links:
            adapter_cls = adapter_registry.for_source(link["source_name"])
            cfg = {"source_id": link["source_name"], "config": {}, "rate_limit": None}
            adapter = adapter_cls(cfg)
            try:
                full, variants = adapter.fetch_full(link.get("source_record_id", ""))
                full_record.update(full)
                all_variants.extend([{**v, "source_name": link["source_name"]} for v in variants])
            except NotImplementedError:
                log.debug("pull_full_report: fetch_full not supported by %s", link["source_name"])

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

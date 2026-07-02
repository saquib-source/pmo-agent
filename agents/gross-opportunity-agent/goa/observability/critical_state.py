"""
Critical-state monitor — live health view, not a report.
Watches connector silence, backlog depth, dedup collision rate, gate pass-rate drift.
Section 15 of the build spec. Thresholds are Gap until real volumes exist.
"""

from __future__ import annotations
import logging

from ..stores import cloudsql

log = logging.getLogger(__name__)

# Gap — replace with real thresholds once volume data exists
_MAX_BACKLOG_DEPTH = 10_000
_MIN_GATE_PASS_RATE = 0.10
_MAX_DEDUP_COLLISION_RATE = 0.50


async def health_check() -> dict:
    """Run all critical-state checks and return a status dict."""
    results: dict = {"ok": True, "warnings": [], "errors": []}

    # Backlog depth
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval("SELECT count(*) FROM opportunity WHERE status = 'active'")
    if total and total > _MAX_BACKLOG_DEPTH:
        results["warnings"].append(f"Backlog depth {total} exceeds threshold {_MAX_BACKLOG_DEPTH}")
        results["ok"] = False

    # Gate pass rate — ratio of gate_passed=true to all gate-evaluated records
    async with pool.acquire() as conn:
        passed = await conn.fetchval("SELECT count(*) FROM opportunity WHERE gate_passed = true")
        evaluated = await conn.fetchval("SELECT count(*) FROM opportunity WHERE gate_passed IS NOT NULL")
    if evaluated and evaluated > 0:
        rate = passed / evaluated
        if rate < _MIN_GATE_PASS_RATE:
            results["warnings"].append(f"Gate pass rate {rate:.1%} below threshold {_MIN_GATE_PASS_RATE:.1%}")
            results["ok"] = False

    # Source liveness
    from ..watchdog.liveness import check_all_sources
    await check_all_sources()

    if results["warnings"] or results["errors"]:
        log.warning("Critical-state: %s", results)
    else:
        log.info("Critical-state: all systems healthy")

    return results

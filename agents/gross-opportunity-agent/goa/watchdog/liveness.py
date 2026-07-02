"""
Liveness watchdog — treats source silence as a monitored failure, not an absence.
If a source goes quiet past its expected cadence, emits a warning then escalates to a human.
Section 6.7 of the build spec.
"""

from __future__ import annotations
import logging
from datetime import datetime, timedelta, timezone

from ..stores import cloudsql, firestore

log = logging.getLogger(__name__)

# Calibrated thresholds — tune with real volumes
_WARN_MULTIPLIER = 2.0    # Warn after 2× expected cadence
_ESCALATE_MULTIPLIER = 4.0  # Escalate after 4× expected cadence


def _cron_to_hours(cron: str) -> float:
    """Rough estimate of expected cadence hours from a cron expression.
    Handles the common patterns only. Gap: use a proper cron parser for edge cases.
    """
    if not cron:
        return 24.0
    parts = cron.strip().split()
    if len(parts) < 5:
        return 24.0
    minute, hour, dom, month, dow = parts[:5]
    if dow != "*":
        return 24.0 * 7  # weekly
    if dom != "*":
        return 24.0 * 30  # monthly
    if hour != "*":
        return 24.0  # daily
    return 1.0  # hourly


async def check_all_sources() -> None:
    """Run liveness check across all enabled sources."""
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        sources = await conn.fetch("SELECT * FROM source_registry WHERE enabled = true")
    for source in sources:
        await _check_source(dict(source))


async def _check_source(source: dict) -> None:
    source_id = source["source_id"]
    cadence_hours = _cron_to_hours(source.get("cadence_cron") or "")
    watermark = await cloudsql.get_watermark(source_id)
    if watermark is None:
        return  # Never run — not a silence condition

    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_run_at FROM watermark WHERE source_id = $1", source_id)
    if row is None or row["last_run_at"] is None:
        return

    last_run = row["last_run_at"]
    if last_run.tzinfo is None:
        last_run = last_run.replace(tzinfo=timezone.utc)

    hours_silent = (datetime.now(timezone.utc) - last_run).total_seconds() / 3600

    if hours_silent >= cadence_hours * _ESCALATE_MULTIPLIER:
        msg = f"SOURCE SILENT {hours_silent:.0f}h — escalating: {source.get('name', source_id)}"
        log.error(msg)
        await firestore.log_activity("source_silent", source_id, msg)
        # Gap: trigger PagerDuty / Slack alert via platform escalation
    elif hours_silent >= cadence_hours * _WARN_MULTIPLIER:
        msg = f"Source quiet {hours_silent:.0f}h (expected every {cadence_hours:.0f}h): {source.get('name', source_id)}"
        log.warning(msg)
        await firestore.log_activity("source_silent", source_id, msg)

"""
Events API — the write-side endpoints the review screen calls.
Matches Data Contract v1.0 exactly. Served over HTTPS from Agent Runtime or Cloud Run.
Section 9 of the build spec.

Wire to a FastAPI or Cloud Run entrypoint; the functions below are the handlers.
"""

from __future__ import annotations
import asyncio
import logging
from datetime import datetime
from typing import Any

from ..stores import cloudsql
from .full_report import enqueue_full_report

log = logging.getLogger(__name__)


# ── Read endpoints ────────────────────────────────────────────────────────────

async def list_opportunities(user_id: str, status: str = "active") -> list[dict]:
    """GET opportunities. Returns list rows with seen_state resolved for this user."""
    rows = await cloudsql.list_opportunities(user_id, status)
    for row in rows:
        links_pool = await cloudsql.get_pool()
        async with links_pool.acquire() as conn:
            links = await conn.fetch(
                "SELECT source_name, source_url, source_record_id FROM source_link WHERE opportunity_id = $1",
                row["opportunity_id"],
            )
        row["source_links"] = [dict(l) for l in links]
    return rows


async def get_counts(user_id: str) -> dict:
    """GET counts. Per-user new/seen counts plus shared totals."""
    return await cloudsql.get_counts(user_id)


async def get_stats() -> dict:
    """GET dashboard stats: funnel, dedup rate, breakdowns by state/type/source, score histogram."""
    return await cloudsql.get_stats()


async def get_activity(since_id: int = 0, limit: int = 100, agent: str | None = None) -> list[dict]:
    """GET the live agent-activity feed (what the agent is doing, per pipeline step).
    `agent` filters to one swarm agent's own log."""
    return await cloudsql.get_activity(since_id, limit, agent)


async def get_agents() -> list[dict]:
    """GET the swarm view: every agent's identity, what it does, its engine binding
    (resolved from the Config Registry — config, never code), live status derived
    from its last activity, and its counters."""
    import os
    from ..agents_meta import AGENTS
    from ..stores.config_registry import resolve
    from ..engine.runner import _apply_overrides, _transport
    from datetime import datetime, timezone

    summaries = {s["agent"]: s for s in await cloudsql.get_agent_summaries()}
    now = datetime.now(timezone.utc)
    out = []
    for a in AGENTS:
        row = dict(a)
        s = summaries.get(a["agent_id"], {})
        row.update({
            "events_total": s.get("events_total", 0),
            "events_today": s.get("events_today", 0),
            "good_total": s.get("good_total", 0),
            "drop_total": s.get("drop_total", 0),
            "warn_total": s.get("warn_total", 0),
            "last_ts": s.get("last_ts"),
            "last_message": s.get("last_message"),
            "last_level": s.get("last_level"),
        })
        # Live status: 'working' if it logged within the last 2 minutes.
        status = "never_ran"
        if row["last_ts"]:
            age = (now - datetime.fromisoformat(row["last_ts"])).total_seconds()
            status = "working" if age < 120 else "idle"
        row["status"] = status
        # Engine binding (display only; resolution stays runtime config).
        if a.get("engine_role"):
            try:
                binding = _apply_overrides(resolve(a["engine_role"]))
                row["engine"] = {"role": a["engine_role"], "version": binding.get("version"),
                                 "effort": binding.get("effort"), "transport": _transport(),
                                 "overridden": bool(os.environ.get("GOA_ENGINE_OVERRIDE_VERSION"))}
            except Exception:
                row["engine"] = {"role": a["engine_role"]}
        out.append(row)
    return out


async def get_budget() -> list[dict]:
    """GET today's API request budget per source: quota, used, remaining, reset time.
    This is how the console proves we can never 429: runs stop at the budget line."""
    import os
    from datetime import datetime, timezone, timedelta
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        sources = await conn.fetch("SELECT source_id, name, enabled FROM source_registry")
    now = datetime.now(timezone.utc)
    reset_at = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for src in sources:
        rl = await cloudsql.get_source_rate_limit(src["source_id"])
        env_rpd = os.environ.get("GOA_REQUESTS_PER_DAY")
        per_day = int(env_rpd) if env_rpd else int(rl.get("requests_per_day") or 0)
        used = await cloudsql.get_requests_used_today(src["source_id"])
        out.append({
            "source_id": src["source_id"],
            "name": src["name"],
            "enabled": src["enabled"],
            "requests_per_day": per_day,          # 0 = unlimited
            "used_today": used,
            "remaining": max(0, per_day - used) if per_day else None,
            "reserve_for_ui": int(rl.get("reserve_for_ui") or 0),
            "override_env": bool(env_rpd),
            "resets_at_utc": reset_at.isoformat(),
        })
    return out


async def open_detail(opportunity_id: str) -> dict | None:
    """Return the detail-surface fields already in the store. No model call."""
    return await cloudsql.get_opportunity(opportunity_id)


# ── Write endpoints (contract events) ────────────────────────────────────────

async def mark_seen(opportunity_id: str, user_id: str, via: str) -> None:
    """Inserts or ignores the seen_event for this user. Never changes status."""
    await cloudsql.mark_seen(opportunity_id, user_id, via)
    log.info("mark_seen: opp=%s user=%s via=%s", opportunity_id, user_id, via)


async def pull_full_report(opportunity_id: str) -> dict:
    """Sets fetch_state = 'pulling', enqueues the async job, returns immediately."""
    await cloudsql.set_fetch_state(opportunity_id, "pulling")
    enqueue_full_report(opportunity_id)
    return {"fetch_state": "pulling", "opportunity_id": opportunity_id}


async def reject_opportunity(
    opportunity_id: str,
    user_id: str,
    reason_text: str,
    rule_scope: str,       # one_time | permanent
    rule_target: str = "initial_screening",  # initial_screening | deep_criteria
) -> None:
    """Reject an opportunity. If permanent, appends a screening rule."""
    await cloudsql.reject_opportunity(opportunity_id, user_id, reason_text, rule_scope,
                                      rule_target if rule_scope == "permanent" else None)
    if rule_scope == "permanent":
        import uuid
        rule = {
            "rule_id": f"hum_{uuid.uuid4().hex[:8]}",
            "list_name": rule_target,
            "description": f"Permanent rejection: {reason_text}",
            "kind": "exclude",
            "field": "project_name_and_body",
            "operator": "matches",
            "value": [reason_text],
            "source": "human",
            "created_by": user_id,
            "active": True,
        }
        await cloudsql.insert_rule(rule)
        log.info("reject_opportunity: permanent rule written to %s", rule_target)
    log.info("reject_opportunity: opp=%s user=%s scope=%s", opportunity_id, user_id, rule_scope)


async def reopen_opportunity(opportunity_id: str) -> None:
    """Set status back to active and clear rejection."""
    await cloudsql.reopen_opportunity(opportunity_id)
    log.info("reopen_opportunity: opp=%s", opportunity_id)


async def edit_criteria(list_name: str, operation: str, rule_or_value: dict) -> None:
    """Add, toggle, or remove a rule in initial_screening or deep_criteria, or update scope.
    Changes to initial_screening and scope take effect on the next gate evaluation.
    """
    if operation == "add":
        await cloudsql.insert_rule({**rule_or_value, "list_name": list_name})
    elif operation == "toggle":
        await cloudsql.toggle_rule(rule_or_value["rule_id"], rule_or_value.get("active", True))
    elif operation == "remove":
        await cloudsql.delete_rule(rule_or_value["rule_id"])
    elif operation == "update_scope":
        field = rule_or_value.get("field")
        value = rule_or_value.get("value", [])
        if field:
            await cloudsql.update_scope_field(field, value)
    else:
        log.warning("edit_criteria: unknown operation '%s'", operation)
    log.info("edit_criteria: list=%s op=%s", list_name, operation)

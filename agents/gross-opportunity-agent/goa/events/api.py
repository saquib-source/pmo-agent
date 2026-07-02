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

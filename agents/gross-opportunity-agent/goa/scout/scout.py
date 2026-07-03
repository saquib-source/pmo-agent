"""
Source discovery scout — finds new aggregators, boards, and feeds, proposes them for human approval.
Tier 2–3: a reasoning engine (resolved at runtime from Config Registry role 'scout_reasoning').
Runs infrequently (weekly Calibrated default). No vendor or model name appears in this file.
Section 6.6 of the build spec. APPROVE gate: the scout never connects a source on its own.
"""

from __future__ import annotations
import asyncio
import logging
import uuid
from datetime import datetime

from ..stores import cloudsql
from ..engine import run_role_json

log = logging.getLogger(__name__)

_SCOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source_id": {"type": "string"},
                    "name": {"type": "string"},
                    "method": {"type": "string"},
                    "url": {"type": "string"},
                    "why_relevant": {"type": "string"},
                },
                "required": ["source_id", "name", "method", "url", "why_relevant"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["candidates"],
    "additionalProperties": False,
}

_SCOUT_SYSTEM = (
    "You are a source-discovery scout for a commercial-construction opportunity pipeline. "
    "Propose NEW public bid boards, permit feeds, and opportunity aggregators the pipeline "
    "may not already cover, relevant to CSI Division 10 (toilet partitions, shower "
    "enclosures), Division 08 (openings/glazing), and Division 22 (plumbing fixtures) in the "
    "United States. You NEVER connect a source — every proposal is written to the registry "
    "as disabled and awaits human APPROVE. Propose real, well-known public sources; do not "
    "invent URLs. Return a list; an empty list is fine if nothing new is warranted."
)


async def run_scout(existing_source_ids: list[str] | None = None) -> list[dict]:
    """Run the source discovery scout. Returns list of candidate sources proposed.
    Every candidate is written disabled/unknown — the human APPROVE gate enables it.
    """
    existing = existing_source_ids or []
    prompt = (
        "Sources already in the registry (do not re-propose):\n"
        f"  {existing}\n\n"
        "Propose new candidate sources for the scope above."
    )
    try:
        result = await asyncio.to_thread(
            run_role_json, "scout_reasoning", _SCOUT_SYSTEM, prompt, _SCOUT_SCHEMA
        )
        candidates = result.get("candidates", [])
    except Exception as e:
        log.warning("Scout run failed: %s", e)
        return []

    for candidate in candidates:
        candidate.setdefault("config", {"url": candidate.get("url")})
        await _propose_source(candidate)

    log.info("Scout: proposed %d candidate source(s) — all awaiting human APPROVE", len(candidates))
    return candidates


async def _propose_source(candidate: dict) -> None:
    """Write a candidate source to source_registry with enabled=false, verified=unknown.
    The APPROVE gate (human Approve step, Runbook Step 5) must set enabled=true.
    """
    source_id = candidate.get("source_id") or f"scout_{uuid.uuid4().hex[:8]}"
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchval(
            "SELECT 1 FROM source_registry WHERE source_id = $1", source_id
        )
        if existing:
            log.info("Scout: source_id=%s already in registry, skipping", source_id)
            return
        import json
        await conn.execute(
            """
            INSERT INTO source_registry (source_id, name, method, config, cadence_cron, verified, enabled)
            VALUES ($1, $2, $3, $4, $5, 'unknown', false)
            """,
            source_id,
            candidate.get("name", source_id),
            candidate.get("method", "rest"),
            json.dumps(candidate.get("config", {})),
            candidate.get("cadence_cron"),
        )
    log.info("Scout: proposed source_id=%s name=%s — awaiting human APPROVE", source_id, candidate.get("name"))

"""
Source discovery scout — finds new aggregators, boards, and feeds, proposes them for human approval.
Tier 2–3: reasoning model with web search. Runs infrequently (weekly Calibrated default).
Config Registry role: scout_reasoning → claude-fable-5 with web_search tool.
Section 6.6 of the build spec. APPROVE gate: the scout never connects a source on its own.
"""

from __future__ import annotations
import logging
import uuid
from datetime import datetime

from ..stores import cloudsql
from ..stores.config_registry import get_version, get_family, get_tools

log = logging.getLogger(__name__)


async def run_scout() -> list[dict]:
    """Run the source discovery scout. Returns list of candidate sources proposed."""
    engine_family = get_family("scout_reasoning")
    engine_version = get_version("scout_reasoning")
    tools = get_tools("scout_reasoning")
    log.info("Scout: running engine=%s %s tools=%s", engine_family, engine_version, tools)

    # Gap: wire to ADK model call with web_search tool
    # The model is prompted with the scope (CSI 10/08/22, shower/partition products, US geographies)
    # and asked to find new public bid boards, permit feeds, and aggregators
    # that the current source registry doesn't cover.
    # It returns a structured list of candidate sources.
    candidates: list[dict] = []  # Stub until ADK runtime is wired

    for candidate in candidates:
        await _propose_source(candidate)

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

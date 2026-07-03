"""
Swarm agent registry — the single place that names each agent in the GOA swarm.

Every agent_activity row is attributed to ONE of these agents so the console can
show per-agent status, per-agent logs, and per-agent counters. The engine `role`
is the Config Registry key the agent resolves at runtime (engines.json) — the
model binding is config, never code, per the Golden Rule.
"""

from __future__ import annotations

# Ordered — the console renders cards in this order.
AGENTS: list[dict] = [
    {
        "agent_id": "orchestrator",
        "name": "Root Orchestrator",
        "icon": "◉",
        "engine_role": None,
        "does": "Coordinates every run: loads source config, holds the request budget, "
                "fans each record through normalize → dedup → gate → commit, advances the watermark.",
    },
    {
        "agent_id": "connector",
        "name": "Source Connector",
        "icon": "⇣",
        "engine_role": None,
        "does": "Pulls records from SAM.gov (keyword/NAICS query plan), enforces the daily "
                "request budget so the source never 429s, fetches full RFP reports on demand.",
    },
    {
        "agent_id": "normalizer",
        "name": "Normalizer",
        "icon": "⬒",
        "engine_role": "normalizer_extraction",
        "does": "Maps raw source records to the canonical schema: CSI division detection, "
                "address cleanup, identity-key derivation. Falls back to the model for messy fields.",
    },
    {
        "agent_id": "dedup",
        "name": "Dedup Agent",
        "icon": "⧉",
        "engine_role": "dedup_ambiguous_merge",
        "does": "Collapses the same project seen twice: identity-key match, fuzzy blocking, "
                "embedding compare, and model arbitration for ambiguous pairs.",
    },
    {
        "agent_id": "gate",
        "name": "Screening Gate",
        "icon": "⛨",
        "engine_role": "gate_classifier",
        "does": "Recall-first coarse gate: deterministic scope rules, then the model classifier "
                "scores borderline records. Keeps anything plausibly in scope.",
    },
    {
        "agent_id": "committer",
        "name": "Committer",
        "icon": "✓",
        "engine_role": None,
        "does": "Atomic idempotent writes: opportunity + source links to Cloud SQL, raw + gross "
                "records to BigQuery, fired-marker so a record is never double-processed.",
    },
    {
        "agent_id": "watchdog",
        "name": "Watchdog",
        "icon": "⏱",
        "engine_role": None,
        "does": "Daily expiration sweep: closes opportunities whose bid date has passed and "
                "flags sources that stopped producing.",
    },
    {
        "agent_id": "scout",
        "name": "Scout",
        "icon": "☌",
        "engine_role": "scout_reasoning",
        "does": "Weekly discovery: reasons about new clean sources worth registering "
                "(state portals, muni bid boards) and drafts their source configs.",
    },
]

# step (agent_activity.step) → owning agent. Explicit `agent=` on log_activity wins.
STEP_TO_AGENT: dict[str, str] = {
    "run": "orchestrator",
    "error": "orchestrator",
    "pull": "connector",
    "budget": "connector",
    "fetch_full": "connector",
    "normalize": "normalizer",
    "dedup": "dedup",
    "gate": "gate",
    "commit": "committer",
    "watchdog": "watchdog",
    "scout": "scout",
}


def agent_for_step(step: str) -> str:
    return STEP_TO_AGENT.get(step, "orchestrator")

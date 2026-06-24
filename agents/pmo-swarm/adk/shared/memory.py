"""
Layer 4 — Memory
Two-tier memory for the PMO swarm:

  Tier 1 (within-run):  ADK session service — context survives within one daemon cycle.
                        Uses VertexAiSessionService if VERTEX_AGENT_ENGINE_ID is set,
                        otherwise InMemorySessionService.

  Tier 2 (cross-run):   AlloyDB agent_memory table with pgvector (768-dim embeddings).
                        The agent remembers per-ticket history, per-assignee patterns,
                        and project baselines across daemon restarts.

Portable artifact: memory_schema.yaml
"""
import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── Tier 1 — ADK session service ─────────────────────────────────────────────

def get_session_service():
    """Return VertexAiSessionService if configured, else InMemorySessionService."""
    try:
        from .config_registry import get_gcp_project, get_vertex_location, get_vertex_engine_id
        project   = get_gcp_project()
        location  = get_vertex_location()
        engine_id = get_vertex_engine_id()
    except Exception:
        import os
        project   = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
        location  = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        engine_id = os.environ.get("VERTEX_AGENT_ENGINE_ID", "")

    if project and engine_id:
        try:
            from google.adk.sessions import VertexAiSessionService
            svc = VertexAiSessionService(
                project=project,
                location=location,
                agent_engine_resource_name=(
                    f"projects/{project}/locations/{location}"
                    f"/reasoningEngines/{engine_id}"
                ),
            )
            log.info(f"Memory Tier 1: VertexAiSessionService — engine={engine_id}")
            return svc
        except Exception as e:
            log.warning(f"Memory Tier 1: Vertex AI unavailable ({e}) — using InMemory")

    log.warning("Memory Tier 1: InMemorySessionService (sessions reset each run)")
    from google.adk.sessions import InMemorySessionService
    return InMemorySessionService()


# ── Tier 2 — AlloyDB pgvector (cross-run persistent memory) ──────────────────

async def store_memory(
    namespace: str,
    session_id: str,
    summary: str,
    metadata: Optional[dict] = None,
    embedding: Optional[list] = None,
) -> bool:
    """Persist a memory entry to AlloyDB agent_memory table.

    namespace: 'tickets' | 'assignees' | 'projects' | 'briefs'
    session_id: ticket key, assignee account ID, project key, or brief timestamp
    summary: human-readable text summary (also used for embedding)
    embedding: 768-dim vector — if None, entry is stored without a vector
    """
    try:
        from .db import get_pool
        from .config_registry import get_tenant_id
        pool = await get_pool()
        if pool is None:
            return False

        tenant_id = get_tenant_id()
        meta = {**(metadata or {}), "namespace": namespace}

        async with pool.acquire() as conn:
            if embedding:
                await conn.execute(
                    """
                    INSERT INTO agent_memory
                      (tenant_id, role_category, session_id, summary, embedding, metadata)
                    VALUES ($1, $2, $3, $4, $5::vector, $6)
                    """,
                    tenant_id,
                    "PMO Orchestrator",
                    session_id,
                    summary[:2000],
                    str(embedding),   # asyncpg passes as text; pgvector casts automatically
                    __import__("json").dumps(meta),
                )
            else:
                await conn.execute(
                    """
                    INSERT INTO agent_memory
                      (tenant_id, role_category, session_id, summary, embedding, metadata)
                    VALUES ($1, $2, $3, $4, NULL, $5)
                    """,
                    tenant_id,
                    "PMO Orchestrator",
                    session_id,
                    summary[:2000],
                    __import__("json").dumps(meta),
                )
        return True

    except Exception as e:
        log.warning(f"Memory Tier 2: store failed for {namespace}/{session_id} ({e})")
        return False


async def recall_memory(
    namespace: str,
    session_id: str,
    limit: int = 5,
) -> list[dict]:
    """Retrieve the most recent memory entries for a given namespace + session_id."""
    try:
        from .db import get_pool
        from .config_registry import get_tenant_id
        pool = await get_pool()
        if pool is None:
            return []

        tenant_id = get_tenant_id()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT session_id, summary, metadata, created_at
                FROM agent_memory
                WHERE tenant_id    = $1
                  AND role_category = 'PMO Orchestrator'
                  AND session_id   = $2
                  AND metadata->>'namespace' = $3
                ORDER BY created_at DESC
                LIMIT $4
                """,
                tenant_id,
                session_id,
                namespace,
                limit,
            )
        return [dict(r) for r in rows]

    except Exception as e:
        log.warning(f"Memory Tier 2: recall failed for {namespace}/{session_id} ({e})")
        return []


async def recall_similar(
    namespace: str,
    query_embedding: list,
    limit: int = 5,
) -> list[dict]:
    """Semantic search over agent_memory using pgvector cosine similarity."""
    try:
        from .db import get_pool
        from .config_registry import get_tenant_id
        pool = await get_pool()
        if pool is None:
            return []

        tenant_id = get_tenant_id()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT session_id, summary, metadata, created_at,
                       1 - (embedding <=> $3::vector) AS similarity
                FROM agent_memory
                WHERE tenant_id    = $1
                  AND role_category = 'PMO Orchestrator'
                  AND metadata->>'namespace' = $2
                  AND embedding IS NOT NULL
                ORDER BY embedding <=> $3::vector
                LIMIT $4
                """,
                tenant_id,
                namespace,
                str(query_embedding),
                limit,
            )
        return [dict(r) for r in rows]

    except Exception as e:
        log.warning(f"Memory Tier 2: similarity search failed for {namespace} ({e})")
        return []


# ── Portable artifact emitter ─────────────────────────────────────────────────

def emit_schema_artifact(output_path: Optional[Path] = None) -> Path:
    """Write memory_schema.yaml — portable artifact #4 of 6."""
    if output_path is None:
        output_path = Path(__file__).parent.parent / "memory_schema.yaml"

    import yaml
    schema = {
        "version": "1.0",
        "swarm": "pmo-swarm",
        "description": (
            "PMO agent persistent memory — AlloyDB agent_memory table (pgvector). "
            "Within-run context via ADK session service."
        ),
        "tier_1": {
            "type": "ADK session service",
            "backend": "VertexAiSessionService → InMemorySessionService fallback",
            "scope": "within a single daemon cycle",
        },
        "tier_2": {
            "type": "pgvector",
            "table": "agent_memory",
            "database": "isrds_agentic (AlloyDB)",
            "embedding_dims": 768,
            "scope": "persistent across daemon restarts",
        },
        "retention_days": 90,
        "namespaces": {
            "tickets": {
                "description": "Per-ticket interaction history",
                "session_id_format": "Jira ticket key (e.g. ASHS-1234)",
                "keys": {
                    "last_contacted_at":    "ISO timestamp of last PMO-drafted comment",
                    "contact_count":        "Total follow-ups sent to this ticket",
                    "stall_duration_hours": "Stall hours at most recent scan",
                    "escalated":            "true if ticket has been escalated",
                    "hygiene_violations":   "Hygiene issue codes on last scan",
                },
            },
            "assignees": {
                "description": "Per-person response patterns",
                "session_id_format": "Jira account ID",
                "keys": {
                    "avg_response_hours": "Rolling average hours to respond",
                    "response_rate":      "Fraction of follow-ups with a reply (0-1)",
                    "escalation_count":   "Times escalated over",
                },
            },
            "projects": {
                "description": "Per-project health baselines",
                "session_id_format": "Jira project key (e.g. ASHS)",
                "keys": {
                    "avg_stall_count":    "Rolling average stalled tickets per scan",
                    "hygiene_score":      "Compliance rate (0.0–1.0)",
                    "feature_pct_built":  "Last known % of features built",
                },
            },
            "briefs": {
                "description": "Past Operating Brief summaries for trend context",
                "session_id_format": "ISO timestamp of the brief",
                "keys": {
                    "stall_count":      "Stalled tickets at time of brief",
                    "gates_triggered":  "Approval gates opened in the cycle",
                    "run_duration_ms":  "Cycle duration in milliseconds",
                },
            },
        },
    }

    output_path.write_text(yaml.dump(schema, default_flow_style=False, sort_keys=False))
    log.info(f"Memory schema artifact written → {output_path}")
    return output_path

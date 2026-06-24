"""
Config Registry — reads per-Role-Category configuration from AlloyDB.
Every ADK agent calls RoleConfig.load(role_category) at init.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

import asyncpg
from dotenv import load_dotenv

load_dotenv()


@dataclass
class RoleConfig:
    role_category: str
    engine_binding: str          # e.g. claude-sonnet-4-6
    memory_surface: str          # pgvector | postgresql_ts | vertex_ai_rag | pgvector+session
    tool_surface: str            # survey_tools | read_only_domain | etc.
    authority_gradient_version: str
    system_prompt: Optional[str]
    swarm_template_id: Optional[str]

    # ── Authority Gradient (loaded from authority_gradient_versions) ──────────
    decision_class: str = "DECIDE_AND_REPORT"  # MUST_ESCALATE | DECIDE_AND_REPORT | DECIDE_SILENTLY
    escalation_triggers: list = None
    autonomy_ceiling: dict = None

    def __post_init__(self):
        if self.escalation_triggers is None:
            self.escalation_triggers = []
        if self.autonomy_ceiling is None:
            self.autonomy_ceiling = {}


async def _get_db_pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        host=os.environ["ALLOYDB_HOST"],
        port=int(os.environ.get("ALLOYDB_PORT", 5432)),
        database=os.environ["ALLOYDB_DATABASE"],
        user=os.environ["ALLOYDB_USER"],
        password=os.environ["ALLOYDB_PASSWORD"],
        min_size=1,
        max_size=5,
    )


# Module-level pool (lazily initialised)
_pool: Optional[asyncpg.Pool] = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await _get_db_pool()
    return _pool


async def load(role_category: str) -> RoleConfig:
    """
    Load RoleConfig for a given Role Category from AlloyDB.
    Raises ValueError if role_category not found in config_registry.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              cr.role_category,
              cr.engine_binding,
              cr.memory_surface,
              cr.tool_surface,
              cr.authority_gradient_version,
              cr.system_prompt,
              cr.swarm_template_id,
              agv.decision_class,
              agv.escalation_triggers,
              agv.autonomy_ceiling
            FROM config_registry cr
            LEFT JOIN authority_gradient_versions agv
              ON agv.role_category = cr.role_category
             AND agv.version       = cr.authority_gradient_version
            WHERE cr.role_category = $1
            """,
            role_category,
        )

    if row is None:
        raise ValueError(
            f"Role category '{role_category}' not found in config_registry. "
            "Run 003_seed_config_registry.sql first."
        )

    return RoleConfig(
        role_category=row["role_category"],
        engine_binding=row["engine_binding"],
        memory_surface=row["memory_surface"],
        tool_surface=row["tool_surface"],
        authority_gradient_version=row["authority_gradient_version"],
        system_prompt=row["system_prompt"],
        swarm_template_id=row["swarm_template_id"],
        decision_class=row["decision_class"] or "DECIDE_AND_REPORT",
        escalation_triggers=list(row["escalation_triggers"] or []),
        autonomy_ceiling=dict(row["autonomy_ceiling"] or {}),
    )

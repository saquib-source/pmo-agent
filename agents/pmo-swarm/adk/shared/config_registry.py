"""
Layer 1 — Config Registry
Single source of truth for PMO swarm configuration.

Priority order:
  1. Cloud SQL Postgres  config_registry table (role_category = 'PMO Orchestrator')
  2. Environment variables (local dev / bootstrap)

Call `await initialize()` once at daemon startup to pull from Cloud SQL Postgres.
All typed accessors are synchronous and read from the in-process cache,
so they work before and after the async init.
"""
import os
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

_cache: dict = {}


# ── Env-var defaults (always available immediately) ───────────────────────────

def _defaults() -> dict:
    return {
        "agent_model":                os.environ.get("AGENT_MODEL", "gemini-2.5-flash"),
        "tenant_id":                  os.environ.get("TENANT_ID", "ashs"),
        "google_cloud_project":       os.environ.get("GOOGLE_CLOUD_PROJECT", "isr-division-systems-488723"),
        "vertex_location":            os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        "vertex_agent_engine_id":     os.environ.get("VERTEX_AGENT_ENGINE_ID", ""),
        "scan_interval_minutes":      int(os.environ.get("PMO_SCAN_INTERVAL_MINUTES", "60")),
        "brief_hour":                 int(os.environ.get("PMO_BRIEF_HOUR", "7")),
        "auto_comment":               os.environ.get("PMO_AUTO_COMMENT", "false").lower() == "true",
        "stall_threshold_hours":      int(os.environ.get("PMO_STALE_THRESHOLD_HOURS", "24")),
        "followup_threshold_hours":   int(os.environ.get("PMO_CHASE_THRESHOLD_HOURS", "48")),
        "escalation_threshold_hours": int(os.environ.get("PMO_ESCALATE_THRESHOLD_HOURS", "72")),
        "jira_projects":              os.environ.get(
                                          "JIRA_PROJECTS",
                                          "ASHS,BAS,BTK,FQ,ISRDS,MDP,SOC,UNCS"
                                      ).split(","),
        "jira_url":                   os.environ.get("JIRA_URL", ""),
        "observability_enabled":      os.environ.get("OBSERVABILITY_ENABLED", "true").lower() == "true",
        "log_level":                  os.environ.get("LOG_LEVEL", "INFO"),
        "memory_surface":             "pgvector+session",
        "decision_class":             "DECIDE_AND_REPORT",
    }


# ── Async init — called once at daemon startup ─────────────────────────────────

async def initialize() -> None:
    """Load config from Cloud SQL Postgres config_registry (role_category = 'PMO Orchestrator').
    Merges DB values on top of env-var defaults.
    Falls back gracefully if Cloud SQL Postgres is not available.
    """
    global _cache
    _cache = _defaults()

    try:
        from .db import get_pool
        pool = await get_pool()
        if pool is None:
            log.info("Config Registry: Cloud SQL Postgres unavailable — using env defaults")
            return

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                  cr.engine_binding,
                  cr.memory_surface,
                  cr.tool_surface,
                  cr.system_prompt,
                  agv.decision_class,
                  agv.escalation_triggers,
                  agv.autonomy_ceiling
                FROM config_registry cr
                LEFT JOIN authority_gradient_versions agv
                  ON agv.role_category = cr.role_category
                 AND agv.version       = cr.authority_gradient_version
                WHERE cr.role_category = 'PMO Orchestrator'
                """,
            )

        if row:
            _cache["agent_model"]    = row["engine_binding"]  or _cache["agent_model"]
            _cache["memory_surface"] = row["memory_surface"]  or _cache["memory_surface"]
            _cache["decision_class"] = row["decision_class"]  or _cache["decision_class"]
            if row["system_prompt"]:
                _cache["system_prompt"] = row["system_prompt"]
            log.info("Config Registry: loaded from Cloud SQL Postgres — role='PMO Orchestrator'"
                     f"  model={_cache['agent_model']}  memory={_cache['memory_surface']}")
        else:
            log.warning(
                "Config Registry: 'PMO Orchestrator' row not found in Cloud SQL Postgres "
                "— run migrations/004_pmo_swarm.sql, using env defaults"
            )

    except Exception as e:
        log.warning(f"Config Registry: Cloud SQL Postgres read failed ({e}) — using env defaults")


# ── Sync getter (reads cache; lazy-initialises from env if called before init) ─

def get(key: str, default: Any = None) -> Any:
    if not _cache:
        _cache.update(_defaults())
    return _cache.get(key, default)


def refresh() -> None:
    global _cache
    _cache = {}


# ── Typed accessors ───────────────────────────────────────────────────────────

def get_agent_model() -> str:
    return str(get("agent_model", "gemini-2.5-flash"))

def adk_model(name: str = ""):
    """Resolved model wrapped for ADK with transport-level retries.

    genai's built-in retry (HttpRetryOptions) covers 408/429/5xx plus
    httpx.ConnectError/TimeoutException — but NOT httpx.ReadError (a dropped
    keep-alive connection), which killed whole agent nodes in production
    (2026-07-16). The subclass below retries any httpx.TransportError around
    the non-streaming call; the streaming path is passed through untouched
    (partial yields can't be safely retried).
    Falls back to the plain name string if the installed ADK/genai don't
    support this, preserving the previous behavior.
    """
    name = name or get_agent_model()
    attempts = int(os.environ.get("PMO_LLM_RETRY_ATTEMPTS", "4"))
    try:
        import asyncio
        import httpx
        from google.adk.models import Gemini
        from google.genai import types as genai_types

        class _RetryingGemini(Gemini):
            async def generate_content_async(self, llm_request, stream=False):
                if stream:
                    async for r in super().generate_content_async(llm_request, stream=True):
                        yield r
                    return
                for attempt in range(1, attempts + 1):
                    try:
                        # Non-streaming yields exactly one final response, so
                        # buffering before re-yielding is behavior-preserving.
                        results = []
                        async for r in super().generate_content_async(llm_request, stream=False):
                            results.append(r)
                        for r in results:
                            yield r
                        return
                    except httpx.TransportError as e:
                        if attempt == attempts:
                            raise
                        log.warning(
                            f"LLM transport error ({type(e).__name__}: {e}) — "
                            f"retrying request {attempt}/{attempts - 1}"
                        )
                        await asyncio.sleep(min(2 ** attempt, 30))

        return _RetryingGemini(
            model=name,
            retry_options=genai_types.HttpRetryOptions(
                attempts=attempts,
                initial_delay=1.0,
                exp_base=2.0,
            ),
        )
    except Exception as e:
        log.warning(f"adk_model: retry wrapper unavailable ({type(e).__name__}: {e}) — using plain model name")
        return name

def get_tenant_id() -> str:
    return str(get("tenant_id", "ashs"))

def get_gcp_project() -> str:
    return str(get("google_cloud_project", "isr-division-systems-488723"))

def get_vertex_location() -> str:
    return str(get("vertex_location", "us-central1"))

def get_vertex_engine_id() -> str:
    return str(get("vertex_agent_engine_id", ""))

def get_scan_interval() -> int:
    return int(get("scan_interval_minutes", 60))

def get_brief_hour() -> int:
    return int(get("brief_hour", 7))

def get_auto_comment() -> bool:
    return bool(get("auto_comment", False))

def get_stall_thresholds() -> dict:
    return {
        "watch":    int(get("stall_threshold_hours", 24)),
        "followup": int(get("followup_threshold_hours", 48)),
        "escalate": int(get("escalation_threshold_hours", 72)),
    }

def get_jira_projects() -> list:
    """Projects the swarm scans. 'ALL' / '*' (env or DB value) resolves to the
    live-discovered list of every project the Jira token can see — briefs and
    prompts always name the real projects, and new Jira projects appear
    automatically on the next run, no redeploy."""
    projs = list(get("jira_projects", ["ASHS", "BAS", "BTK", "FQ", "ISRDS", "MDP", "SOC", "UNCS"]))
    if any(str(p).strip().upper() in ("ALL", "*") for p in projs):
        from .jira_client import ALL_PROJECTS
        return list(ALL_PROJECTS)
    return projs

def is_observability_enabled() -> bool:
    return bool(get("observability_enabled", True))

def get_memory_surface() -> str:
    return str(get("memory_surface", "pgvector+session"))

def get_decision_class() -> str:
    return str(get("decision_class", "DECIDE_AND_REPORT"))

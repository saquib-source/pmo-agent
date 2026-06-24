"""
PostgreSQL connection pool — shared across all PMO swarm modules.
Sets app.tenant_id on every connection for Row-Level Security.

Connection strategy (in priority order):
  1. Cloud SQL Auth Proxy (Unix socket) — when CLOUD_SQL_INSTANCE env var is set.
     Used in Cloud Run (no authorized-networks setup required; auth via ADC).
  2. Direct TCP (asyncpg)               — when CLOUD_SQL_HOST env var is set.
     Used in local development with an authorized IP.

Usage:
    pool = await db.get_pool()
    if pool:
        async with pool.acquire() as conn:
            await conn.execute(...)

    # From a sync context (e.g. trust_ledger_log):
    db.fire_and_forget(some_async_coroutine())
"""
import os
import asyncio
import logging
from typing import Optional

log = logging.getLogger(__name__)

_pool = None
_connector = None     # Cloud SQL Connector instance (kept alive with pool)
_available: Optional[bool] = None


async def get_pool():
    """Return a live asyncpg pool, or None if DB is not configured."""
    global _pool, _connector, _available
    if _available is False:
        return None
    if _pool is not None:
        return _pool

    try:
        import asyncpg
    except ImportError:
        log.warning("DB: asyncpg not installed — DB disabled")
        _available = False
        return None

    try:
        from .config_registry import get_tenant_id
        tenant = get_tenant_id()
    except Exception:
        tenant = os.environ.get("TENANT_ID", "ashs")

    async def _init_conn(conn):
        await conn.execute(f"SET app.tenant_id = '{tenant}'")

    # New CLOUD_SQL_* names preferred; ALLOYDB_* kept as fallback for compatibility.
    db_name = os.environ.get("CLOUD_SQL_DATABASE") or os.environ.get("ALLOYDB_DATABASE", "isrds_agentic")
    db_user = os.environ.get("CLOUD_SQL_USER")     or os.environ.get("ALLOYDB_USER", "postgres")
    db_pass = os.environ.get("CLOUD_SQL_PASSWORD")  or os.environ.get("ALLOYDB_PASSWORD", "")

    # ── Path 1: Cloud SQL Auth Proxy via Unix socket (Cloud Run) ──────────────
    # Cloud Run mounts the proxy socket at /cloudsql/{instance}/.s.PGSQL.5432
    # when `cloudSqlInstance` volume is added to the Job spec.
    instance = os.environ.get("CLOUD_SQL_INSTANCE", "")
    if instance:
        socket_dir = f"/cloudsql/{instance}"
        try:
            _pool = await asyncpg.create_pool(
                host=socket_dir,           # asyncpg looks for .s.PGSQL.5432 here
                port=5432,
                database=db_name,
                user=db_user,
                password=db_pass,
                min_size=1,
                max_size=5,
                init=_init_conn,
            )
            _available = True
            log.info(
                f"Cloud SQL Postgres pool ready (Unix socket) — instance={instance} "
                f"db={db_name} user={db_user} tenant={tenant} pool[min=1,max=5]"
            )
            return _pool
        except Exception as e:
            log.warning(f"Cloud SQL: Unix socket pool failed ({e}) — DB disabled")
            _available = False
            return None

    # ── Path 2: Direct TCP (local dev) ────────────────────────────────────────
    host = os.environ.get("CLOUD_SQL_HOST") or os.environ.get("ALLOYDB_HOST", "")
    if not host:
        log.warning("DB: neither CLOUD_SQL_INSTANCE nor CLOUD_SQL_HOST set — DB disabled")
        _available = False
        return None

    try:
        _pool = await asyncpg.create_pool(
            host=host,
            port=int(os.environ.get("CLOUD_SQL_PORT") or os.environ.get("ALLOYDB_PORT", "5432")),
            database=db_name,
            user=db_user,
            password=db_pass,
            min_size=1,
            max_size=5,
            init=_init_conn,
            ssl="require",
        )
        _available = True
        log.info(f"Cloud SQL Postgres pool ready (TCP) — {host}/{db_name} user={db_user} tenant={tenant}")
    except Exception as e:
        log.warning(f"DB: pool failed ({e}) — falling back to local storage")
        _available = False
        _pool = None

    return _pool


def fire_and_forget(coro) -> None:
    """Schedule a coroutine in the running event loop without blocking.
    Used to write to Cloud SQL Postgres from synchronous callers (e.g. trust_ledger_log).
    Silently skipped if no event loop is running (unit tests, CLI).
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        pass

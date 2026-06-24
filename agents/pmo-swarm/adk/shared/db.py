"""
AlloyDB (PostgreSQL on GCP) connection pool — shared across all PMO swarm modules.
Sets app.tenant_id on every connection for Row-Level Security.

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

_pool = None          # asyncpg.Pool once initialised
_available: Optional[bool] = None   # None = not yet tried


async def get_pool():
    """Return a live asyncpg pool, or None if AlloyDB is not configured."""
    global _pool, _available
    if _available is False:
        return None
    if _pool is not None:
        return _pool

    try:
        import asyncpg
    except ImportError:
        log.warning("DB: asyncpg not installed — AlloyDB disabled")
        _available = False
        return None

    host = os.environ.get("ALLOYDB_HOST", "")
    if not host:
        log.warning("DB: ALLOYDB_HOST not set — AlloyDB disabled")
        _available = False
        return None

    try:
        from .config_registry import get_tenant_id
        tenant = get_tenant_id()
    except Exception:
        tenant = os.environ.get("TENANT_ID", "ashs")

    async def _init_conn(conn):
        await conn.execute(f"SET app.tenant_id = '{tenant}'")

    try:
        _pool = await asyncpg.create_pool(
            host=host,
            port=int(os.environ.get("ALLOYDB_PORT", "5432")),
            database=os.environ.get("ALLOYDB_DATABASE", "isrds_agentic"),
            user=os.environ["ALLOYDB_USER"],
            password=os.environ["ALLOYDB_PASSWORD"],
            min_size=1,
            max_size=10,
            init=_init_conn,
        )
        _available = True
        log.info(
            f"AlloyDB pool ready — {host}/{os.environ.get('ALLOYDB_DATABASE', 'isrds_agentic')}"
            f"  tenant={tenant}"
        )
    except Exception as e:
        log.warning(f"DB: AlloyDB pool failed ({e}) — falling back to local storage")
        _available = False
        _pool = None

    return _pool


def fire_and_forget(coro) -> None:
    """Schedule a coroutine in the running event loop without blocking.
    Used to write to AlloyDB from synchronous callers (e.g. trust_ledger_log).
    Silently skipped if no event loop is running (unit tests, CLI).
    """
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        pass

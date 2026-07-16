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

_connector = None     # Cloud SQL Connector instance (kept alive with pool)
_available: Optional[bool] = None
# asyncpg pools are bound to the event loop that created them. ADK tools run in
# their own worker-thread loops (see follow_up._run_async), so cache one pool per
# loop id rather than a single global — reusing a pool across loops corrupts state.
_pools: dict = {}


async def get_pool():
    """Return a live asyncpg pool for the current event loop, or None if unconfigured."""
    global _connector, _available
    if _available is False:
        return None

    loop_key = id(asyncio.get_event_loop())
    existing = _pools.get(loop_key)
    if existing is not None:
        return existing

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
            pool = await asyncpg.create_pool(
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
            _pools[loop_key] = pool
            log.info(
                f"Cloud SQL Postgres pool ready (Unix socket) — instance={instance} "
                f"db={db_name} user={db_user} tenant={tenant} pool[min=1,max=5]"
            )
            return pool
        except Exception as e:
            log.warning(f"Cloud SQL: Unix socket pool failed ({e}) — DB unavailable this loop")
            return None

    # ── Path 2: Direct TCP (local dev) ────────────────────────────────────────
    host = os.environ.get("CLOUD_SQL_HOST") or os.environ.get("ALLOYDB_HOST", "")
    if not host:
        log.warning("DB: neither CLOUD_SQL_INSTANCE nor CLOUD_SQL_HOST set — DB disabled")
        _available = False
        return None

    try:
        pool = await asyncpg.create_pool(
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
        _pools[loop_key] = pool
        log.info(f"Cloud SQL Postgres pool ready (TCP) — {host}/{db_name} user={db_user} tenant={tenant}")
        return pool
    except Exception as e:
        log.warning(f"DB: pool failed ({e}) — falling back to local storage")
        return None


_bg_tasks: set = set()


def _bg_task_done(task) -> None:
    _bg_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()  # retrieve it so asyncio never logs "exception was never retrieved"
    if exc is not None:
        log.warning(f"DB background task failed: {exc!r}")


def fire_and_forget(coro) -> None:
    """Schedule a coroutine in the running event loop without blocking.
    Used to write to Cloud SQL Postgres from synchronous callers (e.g. trust_ledger_log).
    Silently skipped if no event loop is running (unit tests, CLI).
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        coro.close()  # no loop — close so Python doesn't warn "coroutine was never awaited"
        return
    task = loop.create_task(coro)
    _bg_tasks.add(task)  # strong ref: the loop only keeps a weak one
    task.add_done_callback(_bg_task_done)


# DDL guards: fire-and-forget writers all call ensure-table first, so several tasks
# can hit CREATE TABLE IF NOT EXISTS at once. That statement is NOT concurrency-safe
# in Postgres (both sessions pass the existence check, then collide inserting into
# the pg_type catalog → UniqueViolationError on pg_type_typname_nsp_index).
_schema_ready: set = set()   # (loop_id, key) already ensured this process
_schema_locks: dict = {}     # (loop_id, key) -> asyncio.Lock (locks are loop-bound)


async def ensure_schema_once(key: str, statements: list) -> bool:
    """Run DDL statements once per event loop, safe under any concurrency.

    Within a loop: per-key asyncio.Lock + done-set. Across connections, threads
    and job instances: a pg advisory xact lock serializes the DDL, and duplicate-
    object errors from a lost race are treated as success (the object exists).
    Returns True once the schema is known to exist, False if the DB is unavailable.
    """
    loop_key = (id(asyncio.get_running_loop()), key)
    if loop_key in _schema_ready:
        return True
    lock = _schema_locks.setdefault(loop_key, asyncio.Lock())
    async with lock:
        if loop_key in _schema_ready:
            return True
        pool = await get_pool()
        if pool is None:
            return False
        import asyncpg
        try:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        "SELECT pg_advisory_xact_lock(hashtext($1))", f"pmo_ddl:{key}"
                    )
                    for stmt in statements:
                        await conn.execute(stmt)
        except (
            asyncpg.exceptions.UniqueViolationError,
            asyncpg.exceptions.DuplicateTableError,
            asyncpg.exceptions.DuplicateObjectError,
        ):
            pass  # another worker created it first — object exists, which is the goal
        _schema_ready.add(loop_key)
        return True

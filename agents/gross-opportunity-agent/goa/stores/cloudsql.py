"""
Cloud SQL (PostgreSQL 15) store — the transactional serving layer.
The review screen reads and writes through the events API, which calls these functions.
Gap: Cloud SQL instance connection string and credentials.
"""

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import asyncpg  # type: ignore

log = logging.getLogger(__name__)

_pool: asyncpg.Pool | None = None


def _resolve_dsn() -> str:
    """Resolve the Cloud SQL DSN. Prefer CLOUDSQL_DSN env; else read the Secret
    Manager secret named by CLOUDSQL_DSN_SECRET (default goa-cloudsql-dsn) using
    GOOGLE_CLOUD_PROJECT. Real resolution — no placeholder."""
    dsn = os.environ.get("CLOUDSQL_DSN")
    if dsn:
        return dsn
    secret_name = os.environ.get("CLOUDSQL_DSN_SECRET", "goa-cloudsql-dsn")
    project = os.environ.get("GOOGLE_CLOUD_PROJECT")
    if project:
        try:
            from google.cloud import secretmanager  # type: ignore
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{project}/secrets/{secret_name}/versions/latest"
            resp = client.access_secret_version(name=name)
            return resp.payload.data.decode()
        except Exception as e:
            raise RuntimeError(
                f"Could not resolve Cloud SQL DSN from env or Secret Manager "
                f"({secret_name} in {project}): {e}"
            )
    raise RuntimeError(
        "Cloud SQL DSN not set — provide CLOUDSQL_DSN, or GOOGLE_CLOUD_PROJECT "
        "so it can be read from Secret Manager secret goa-cloudsql-dsn."
    )


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        dsn = _resolve_dsn()
        # Cloud SQL requires TLS. If the DSN doesn't already specify sslmode,
        # enforce it. CLOUDSQL_SSLMODE can override (e.g. verify-full with a CA).
        sslmode = os.environ.get("CLOUDSQL_SSLMODE", "require")
        connect_kwargs: dict = {}
        if "sslmode=" not in dsn and "ssl=" not in dsn:
            connect_kwargs["ssl"] = sslmode
        _pool = await asyncpg.create_pool(dsn, min_size=2, max_size=10, **connect_kwargs)
    return _pool


# ── Opportunity ───────────────────────────────────────────────────────────────

async def upsert_opportunity(opp: Any) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO opportunity (
                opportunity_id, project_identity_key, project_name, record_type, stage,
                status, street, city, state, postal_code, country, owner,
                valuation, bid_date, csi_divisions, gate_passed, gate_score,
                gate_matched_rules, primary_source_url, closed_reason,
                agent_trace, first_seen_at, last_changed_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23)
            ON CONFLICT (opportunity_id) DO UPDATE SET
                project_name       = EXCLUDED.project_name,
                stage              = EXCLUDED.stage,
                status             = EXCLUDED.status,
                valuation          = EXCLUDED.valuation,
                bid_date           = EXCLUDED.bid_date,
                csi_divisions      = EXCLUDED.csi_divisions,
                gate_passed        = EXCLUDED.gate_passed,
                gate_score         = EXCLUDED.gate_score,
                gate_matched_rules = EXCLUDED.gate_matched_rules,
                closed_reason      = EXCLUDED.closed_reason,
                agent_trace        = EXCLUDED.agent_trace,
                last_changed_at    = EXCLUDED.last_changed_at
            """,
            opp.opportunity_id, opp.project_identity_key, opp.project_name, opp.record_type,
            opp.stage, opp.status, opp.address.street, opp.address.city, opp.address.state,
            opp.address.postal_code, opp.address.country, opp.owner,
            opp.valuation, opp.bid_date, json.dumps(opp.csi_divisions),
            opp.gate_passed, opp.gate_score, json.dumps(opp.gate_matched_rules),
            opp.primary_source_url, opp.closed_reason,
            json.dumps(getattr(opp, "agent_trace", None)),
            opp.first_seen_at or datetime.utcnow(), opp.last_changed_at or datetime.utcnow(),
        )


async def get_opportunity(opportunity_id: str) -> dict | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM opportunity WHERE opportunity_id = $1", opportunity_id
        )
    return dict(row) if row else None


async def list_opportunities(user_id: str, status: str = "active") -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT o.*,
                   CASE WHEN s.user_id IS NULL THEN 'new' ELSE 'seen' END AS seen_state
            FROM opportunity o
            LEFT JOIN seen_event s
                ON s.opportunity_id = o.opportunity_id AND s.user_id = $1
            WHERE o.status = $2
            ORDER BY o.gate_score DESC NULLS LAST
            """,
            user_id, status,
        )
    return [dict(r) for r in rows]


async def get_counts(user_id: str) -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
              (SELECT count(*) FROM opportunity WHERE status = 'active')    AS total_active,
              (SELECT count(*) FROM opportunity WHERE status = 'rejected')  AS rejected_count,
              (SELECT count(*) FROM opportunity WHERE status = 'closed')    AS closed_count,
              (SELECT count(*) FROM opportunity o WHERE o.status = 'active'
                 AND NOT EXISTS (SELECT 1 FROM seen_event s
                                  WHERE s.opportunity_id = o.opportunity_id
                                    AND s.user_id = $1))                   AS new_count,
              (SELECT count(*) FROM seen_event s
                 JOIN opportunity o ON o.opportunity_id = s.opportunity_id
                 WHERE o.status = 'active' AND s.user_id = $1)             AS seen_count
            """,
            user_id,
        )
    return dict(row)


async def log_activity(step: str, message: str, *, run_id: str | None = None,
                       source_id: str | None = None, opportunity_id: str | None = None,
                       level: str = "info", detail: dict | None = None,
                       agent: str | None = None) -> None:
    """Append one agent-activity event (the live 'what the agent is doing' feed).
    Every event is attributed to ONE swarm agent (explicit `agent` or derived from
    the step). Best-effort: never let logging break the pipeline."""
    from ..agents_meta import agent_for_step
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO agent_activity (run_id, source_id, step, level, message, opportunity_id, detail, agent)
                   VALUES ($1,$2,$3,$4,$5,$6,$7,$8)""",
                run_id, source_id, step, level, message, opportunity_id,
                json.dumps(detail) if detail is not None else None,
                agent or agent_for_step(step),
            )
    except Exception as e:
        log.debug("log_activity failed (%s): %s", step, e)


async def get_activity(since_id: int = 0, limit: int = 100, agent: str | None = None) -> list[dict]:
    """Recent agent-activity events, oldest→newest within the window. `since_id`
    lets the UI poll incrementally (pass the max id it has seen). `agent` filters
    the feed to one swarm agent's log."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        if agent:
            rows = await conn.fetch(
                """SELECT id, ts, run_id, source_id, step, level, message, opportunity_id, detail, agent
                   FROM agent_activity WHERE id > $1 AND agent = $3 ORDER BY id DESC LIMIT $2""",
                since_id, limit, agent,
            )
        else:
            rows = await conn.fetch(
                """SELECT id, ts, run_id, source_id, step, level, message, opportunity_id, detail, agent
                   FROM agent_activity WHERE id > $1 ORDER BY id DESC LIMIT $2""",
                since_id, limit,
            )
    out = [dict(r) for r in rows]
    for r in out:
        if r.get("ts"):
            r["ts"] = r["ts"].isoformat()
    return list(reversed(out))


async def get_agent_summaries() -> list[dict]:
    """Per-agent aggregates for the console's swarm view: event counts, today's
    activity, gate keep/drop split, last event. Joined against the static registry
    in goa/agents_meta.py by the API layer."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT agent,
                   count(*)                                                    AS events_total,
                   count(*) FILTER (WHERE ts >= date_trunc('day', now()))      AS events_today,
                   count(*) FILTER (WHERE level = 'good')                      AS good_total,
                   count(*) FILTER (WHERE level = 'drop')                      AS drop_total,
                   count(*) FILTER (WHERE level = 'warn')                      AS warn_total,
                   max(ts)                                                     AS last_ts
            FROM agent_activity
            WHERE agent IS NOT NULL
            GROUP BY agent
            """
        )
        last_msgs = await conn.fetch(
            """
            SELECT DISTINCT ON (agent) agent, message, level, ts
            FROM agent_activity WHERE agent IS NOT NULL
            ORDER BY agent, id DESC
            """
        )
    by_agent = {r["agent"]: dict(r) for r in rows}
    for m in last_msgs:
        if m["agent"] in by_agent:
            by_agent[m["agent"]]["last_message"] = m["message"]
            by_agent[m["agent"]]["last_level"] = m["level"]
    out = []
    for a, d in by_agent.items():
        if d.get("last_ts"):
            d["last_ts"] = d["last_ts"].isoformat()
        out.append(d)
    return out


# ── API request-budget ledger ─────────────────────────────────────────────────
# One row per (source, UTC day). SAM.gov's quota resets at 00:00 UTC, so the
# ledger keys on the UTC date — used-today survives process restarts and is
# shared between the scheduled job and the console's on-demand full pulls.

async def get_requests_used_today(source_id: str) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        used = await conn.fetchval(
            """SELECT requests_used FROM api_request_ledger
               WHERE source_id = $1 AND day = (now() AT TIME ZONE 'utc')::date""",
            source_id,
        )
    return used or 0


async def record_requests_used(source_id: str, n: int) -> int:
    """Add n requests to today's ledger row (UTC day). Returns the new total."""
    if n <= 0:
        return await get_requests_used_today(source_id)
    pool = await get_pool()
    async with pool.acquire() as conn:
        total = await conn.fetchval(
            """
            INSERT INTO api_request_ledger (source_id, day, requests_used, updated_at)
            VALUES ($1, (now() AT TIME ZONE 'utc')::date, $2, now())
            ON CONFLICT (source_id, day) DO UPDATE SET
                requests_used = api_request_ledger.requests_used + EXCLUDED.requests_used,
                updated_at    = now()
            RETURNING requests_used
            """,
            source_id, n,
        )
    return total or n


async def get_source_rate_limit(source_id: str) -> dict:
    """The rate_limit JSON from source_registry (requests_per_day, reserve_for_ui…)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        raw = await conn.fetchval(
            "SELECT rate_limit FROM source_registry WHERE source_id = $1", source_id
        )
    if raw is None:
        return {}
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


async def get_stats() -> dict:
    """Funnel + breakdown stats for the dashboard. Shared (not per-user)."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        totals = await conn.fetchrow(
            """
            SELECT
              count(*)                                             AS total,
              count(*) FILTER (WHERE status='active')              AS active,
              count(*) FILTER (WHERE status='rejected')            AS rejected,
              count(*) FILTER (WHERE status='closed')              AS closed,
              count(*) FILTER (WHERE gate_passed)                  AS gate_kept,
              count(*) FILTER (WHERE gate_passed IS FALSE)         AS gate_dropped,
              count(*) FILTER (WHERE fetch_state='full_pulled')    AS full_pulled
            FROM opportunity
            """
        )
        by_state = await conn.fetch(
            "SELECT state, count(*) c FROM opportunity WHERE status='active' AND state<>'' GROUP BY state ORDER BY c DESC LIMIT 10"
        )
        by_type = await conn.fetch(
            "SELECT record_type, count(*) c FROM opportunity WHERE status='active' GROUP BY record_type ORDER BY c DESC"
        )
        by_source = await conn.fetch(
            "SELECT source_name, count(DISTINCT opportunity_id) c FROM source_link GROUP BY source_name ORDER BY c DESC"
        )
        link_total = await conn.fetchval("SELECT count(*) FROM source_link")
        opp_total = await conn.fetchval("SELECT count(*) FROM opportunity")
        score_buckets = await conn.fetch(
            """
            SELECT width_bucket(coalesce(gate_score,0)::numeric, 0, 1, 5) AS b, count(*) c
            FROM opportunity WHERE status='active' GROUP BY b ORDER BY b
            """
        )
    return {
        "totals": dict(totals),
        "by_state": [dict(r) for r in by_state],
        "by_record_type": [dict(r) for r in by_type],
        "by_source": [dict(r) for r in by_source],
        "dedup": {"source_records": link_total, "opportunities": opp_total,
                  "collapsed": max(0, link_total - opp_total)},
        "score_buckets": [dict(r) for r in score_buckets],
    }


# ── Source links ──────────────────────────────────────────────────────────────

async def upsert_source_link(opportunity_id: str, source_name: str, source_url: str | None, source_record_id: str | None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO source_link (opportunity_id, source_name, source_url, source_record_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
            """,
            opportunity_id, source_name, source_url, source_record_id,
        )


# ── Seen events ───────────────────────────────────────────────────────────────

async def mark_seen(opportunity_id: str, user_id: str, via: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO seen_event (opportunity_id, user_id, via)
            VALUES ($1, $2, $3)
            ON CONFLICT (opportunity_id, user_id) DO NOTHING
            """,
            opportunity_id, user_id, via,
        )


# ── Rejection ─────────────────────────────────────────────────────────────────

async def reject_opportunity(opportunity_id: str, rejected_by: str, reason_text: str, rule_scope: str, rule_target: str | None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE opportunity SET status = 'rejected', last_changed_at = now() WHERE opportunity_id = $1",
                opportunity_id,
            )
            await conn.execute(
                """
                INSERT INTO rejection (opportunity_id, rejected_by, reason_text, rule_scope, rule_target)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (opportunity_id) DO UPDATE SET
                    rejected_by = EXCLUDED.rejected_by,
                    rejected_at = now(),
                    reason_text = EXCLUDED.reason_text,
                    rule_scope  = EXCLUDED.rule_scope,
                    rule_target = EXCLUDED.rule_target
                """,
                opportunity_id, rejected_by, reason_text, rule_scope, rule_target,
            )


async def reopen_opportunity(opportunity_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "UPDATE opportunity SET status = 'active', last_changed_at = now() WHERE opportunity_id = $1",
                opportunity_id,
            )
            await conn.execute(
                "UPDATE rejection SET rejected = false WHERE opportunity_id = $1",
                opportunity_id,
            )


# ── Fetch state machine ────────────────────────────────────────────────────────

async def set_fetch_state(opportunity_id: str, state: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE opportunity SET fetch_state = $2, last_changed_at = now() WHERE opportunity_id = $1",
            opportunity_id, state,
        )


async def set_fetch_error(opportunity_id: str, error: dict) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE opportunity SET fetch_state = 'failed', fetch_error = $2, last_changed_at = now() WHERE opportunity_id = $1",
            opportunity_id, json.dumps(error),
        )


async def merge_full_record(opportunity_id: str, full_record: dict) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE opportunity SET full_record = $2, last_changed_at = now() WHERE opportunity_id = $1",
            opportunity_id, json.dumps(full_record),
        )


async def add_spec_variants(opportunity_id: str, variants: list[dict]) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        for v in variants:
            await conn.execute(
                """
                INSERT INTO spec_variant (opportunity_id, source_name, label, url, fetched_at)
                VALUES ($1, $2, $3, $4, now())
                ON CONFLICT DO NOTHING
                """,
                opportunity_id, v.get("source_name"), v.get("label"), v.get("url"),
            )


# ── Screening rules ───────────────────────────────────────────────────────────

async def get_rules(list_name: str) -> list[dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT * FROM screening_rule WHERE list_name = $1 AND active = true ORDER BY created_at",
            list_name,
        )
    return [dict(r) for r in rows]


async def insert_rule(rule: dict) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO screening_rule
                (rule_id, list_name, description, kind, field, operator, value, source, owned_by, created_by, active)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            ON CONFLICT (rule_id) DO NOTHING
            """,
            rule["rule_id"], rule["list_name"], rule.get("description"), rule["kind"],
            rule["field"], rule["operator"], json.dumps(rule.get("value")),
            rule.get("source", "human"), rule.get("owned_by"), rule.get("created_by"), rule.get("active", True),
        )


async def toggle_rule(rule_id: str, active: bool) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE screening_rule SET active = $2 WHERE rule_id = $1",
            rule_id, active,
        )


async def delete_rule(rule_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM screening_rule WHERE rule_id = $1", rule_id)


# ── Scope ─────────────────────────────────────────────────────────────────────

async def get_scope() -> dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM scope WHERE id = 1")
    return dict(row) if row else {}


async def update_scope_field(field: str, value: list) -> None:
    allowed = {"csi_divisions", "product_scope", "project_types", "geographies", "hard_excludes"}
    if field not in allowed:
        raise ValueError(f"Unknown scope field: {field}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            f"UPDATE scope SET {field} = $1 WHERE id = 1", json.dumps(value)
        )


# ── Idempotency ───────────────────────────────────────────────────────────────

async def check_and_mark_fired(source_id: str, record_hash: str, opportunity_id: str) -> bool:
    """Atomic: check fired_marker and insert in one transaction.
    Returns True if this was a new record (not a duplicate). Returns False if already fired.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            existing = await conn.fetchval(
                "SELECT 1 FROM fired_marker WHERE source_id=$1 AND source_record_hash=$2",
                source_id, record_hash,
            )
            if existing:
                return False
            await conn.execute(
                "INSERT INTO fired_marker (source_id, source_record_hash, opportunity_id) VALUES ($1,$2,$3)",
                source_id, record_hash, opportunity_id,
            )
    return True


# ── Watermark ─────────────────────────────────────────────────────────────────

async def get_watermark(source_id: str) -> str | None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT last_cursor FROM watermark WHERE source_id = $1", source_id)
    return row["last_cursor"] if row else None


async def advance_watermark(source_id: str, cursor: str | None) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO watermark (source_id, last_cursor, last_run_at) VALUES ($1, $2, now())
            ON CONFLICT (source_id) DO UPDATE SET last_cursor = $2, last_run_at = now()
            """,
            source_id, cursor,
        )

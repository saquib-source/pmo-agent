"""
ISRDS Governance middleware (Authority Gradient + Trust Ledger).
Layer 5 — Policy/Governance: governance-rules.yaml + authority gradient + trust ledger.

Trust Ledger write order (all three happen on every entry):
  1. Local JSONL   — immediate, sync, always works (adk/trust-ledger.jsonl)
  2. BigQuery      — async, fire-and-forget to isrds_pmo.trust_events (system of record)
  3. Cloud Logging — async, fire-and-forget via observability (Layer 8)

Note: the Postgres trust_ledger table (001_foundation.sql) is owned by the Survey Agent
(survey_sessions references it via FK). PMO writes exclusively to BigQuery trust_events.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from .observability import log_event as _obs_log
except ImportError:
    def _obs_log(*a, **kw): pass

_AGENT_DIR = Path(__file__).parent.parent.parent  # pmo-swarm root

try:
    import yaml
    _GOV_PATH = _AGENT_DIR / "governance-rules.yaml"
    GOVERNANCE = yaml.safe_load(_GOV_PATH.read_text()) if _GOV_PATH.exists() else {}
except ImportError:
    GOVERNANCE = {}

LEDGER_FILE = os.environ.get(
    "TRUST_LEDGER_PATH",
    str(_AGENT_DIR / "adk" / "trust-ledger.jsonl"),
)


# ── Authority Gradient ────────────────────────────────────────────────────────

def governance_check(action: str, is_irreversible: bool = False) -> dict:
    """Check if an action is permitted under the governance rules YAML."""
    for rule in GOVERNANCE.get("rules", []):
        if rule.get("action") == action:
            decision = rule.get("decision", "allow")
            if decision == "deny":
                return {"allowed": False, "gate": None, "reason": rule.get("rationale")}
            if decision == "gate":
                return {"allowed": False, "gate": rule.get("gate"), "reason": rule.get("rationale")}
    if is_irreversible:
        return {"allowed": False, "gate": "Approve", "reason": "Irreversible action → Approve gate required"}
    return {"allowed": True, "gate": None, "reason": "Permitted"}


# ── Trust Ledger (append-only) ────────────────────────────────────────────────

def trust_ledger_log(entry_type: str, detail: str, agent_id: str = "pmo_swarm") -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type":      entry_type,
        "detail":    detail[:500],
        "agent_id":  agent_id,
    }

    # 1. Local JSONL — sync, immediate, always works
    with open(LEDGER_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")

    # 2. BigQuery trust_events — async fire-and-forget (system of record)
    try:
        from .analytics import log_trust_event
        from .db import fire_and_forget
        fire_and_forget(log_trust_event(entry_type, detail, agent_id=agent_id))
    except Exception:
        pass  # never block the caller

    # 3. Cloud Logging — async fire-and-forget (Layer 8)
    _obs_log(entry_type, detail, agent_id=agent_id)


async def _ensure_pending_actions_table() -> None:
    from .db import get_pool
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pending_actions (
              id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
              tenant_id     TEXT        NOT NULL,
              swarm_id      TEXT        NOT NULL DEFAULT 'pmo-swarm',
              agent_id      TEXT        NOT NULL,
              action_type   TEXT        NOT NULL,   -- 'comment' | 'transition'
              ticket_key    TEXT        NOT NULL,
              assignee_name TEXT,
              assignee_id   TEXT,
              message       TEXT        NOT NULL,    -- the REAL, ready-to-post body
              urgency       TEXT,
              status        TEXT        NOT NULL DEFAULT 'pending',  -- pending|approved|declined
              created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
              resolved_at   TIMESTAMPTZ,
              resolved_by   TEXT
            )
            """
        )


async def _insert_pending_action(
    tenant_id: str, agent_id: str, action_type: str, ticket_key: str,
    message: str, assignee_name: str = "", assignee_id: str = "", urgency: str = "",
) -> None:
    from .db import get_pool
    await _ensure_pending_actions_table()
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        # Avoid duplicate pending rows for the same ticket+message.
        await conn.execute(
            """
            INSERT INTO pending_actions
              (tenant_id, agent_id, action_type, ticket_key,
               assignee_name, assignee_id, message, urgency)
            SELECT $1,$2,$3,$4,$5,$6,$7,$8
            WHERE NOT EXISTS (
              SELECT 1 FROM pending_actions
              WHERE ticket_key=$4 AND status='pending' AND message=$7
            )
            """,
            tenant_id, agent_id, action_type, ticket_key,
            assignee_name, assignee_id, message, urgency,
        )


def queue_pending_action(
    agent_id: str, action_type: str, ticket_key: str, message: str,
    assignee_name: str = "", assignee_id: str = "", urgency: str = "",
) -> None:
    """Persist a fully-formed, ready-to-post action for human approval (fire-and-forget).
    The UI reads pending_actions and posts `message` verbatim — so what gets posted to
    Jira is the actual human-voiced message, not a meta description.

    SINGLE ELIGIBILITY CHOKEPOINT: every comment draft path funnels through here
    (stall chase, hygiene notify, reply interpretation). Comments on ineligible
    tickets — backlog (no active sprint), Done, or To Do — are dropped here so they
    never reach the human approval queue, not just blocked at post time."""
    try:
        if action_type == "comment":
            from . import jira_client as jc
            elig = jc.is_comment_eligible(ticket_key)
            if not elig["eligible"]:
                trust_ledger_log(
                    "skip-followup",
                    f"Did not queue comment on {ticket_key}: {elig['reason']}",
                    agent_id=agent_id,
                )
                return

        from .config_registry import get_tenant_id
        from .db import fire_and_forget
        fire_and_forget(_insert_pending_action(
            get_tenant_id(), agent_id, action_type, ticket_key,
            message, assignee_name, assignee_id, urgency))
    except Exception:
        pass


def trust_ledger_read(last_n: int = 50) -> list:
    """Read from local JSONL (fast path). Cloud SQL Postgres is the durable store for reporting."""
    if not os.path.exists(LEDGER_FILE):
        return []
    with open(LEDGER_FILE) as f:
        lines = f.readlines()
    entries = []
    for line in lines[-last_n:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return entries


async def trust_ledger_read_db(last_n: int = 50, tenant_id: str = "") -> list:
    """Read trust ledger from BigQuery trust_events — used for reporting and the Operating Brief.
    Falls back to local JSONL if BigQuery is unavailable.
    """
    try:
        from .analytics import _client as _bq_client, _table
        from .config_registry import get_tenant_id
        bq = _bq_client()
        if bq is None:
            return trust_ledger_read(last_n)
        if not tenant_id:
            tenant_id = get_tenant_id()
        query = f"""
            SELECT agent_id, event_type, detail, outcome, event_ts AS created_at
            FROM `{_table('trust_events')}`
            WHERE tenant_id = @tenant_id
              AND swarm_id  = 'pmo-swarm'
            ORDER BY event_ts DESC
            LIMIT {int(last_n)}
        """
        from google.cloud import bigquery
        job = bq.query(
            query,
            job_config=bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("tenant_id", "STRING", tenant_id),
                ]
            ),
        )
        return [dict(row) for row in job.result()]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"trust_ledger BQ read failed ({e}) — using local JSONL")
        return trust_ledger_read(last_n)

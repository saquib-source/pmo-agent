"""
ISRDS Governance middleware (Authority Gradient + Trust Ledger).
Layer 5 — Policy/Governance: governance-rules.yaml + authority gradient + trust ledger.

Trust Ledger write order (all three happen on every entry):
  1. Local JSONL  — immediate, sync, always works (adk/trust-ledger.jsonl)
  2. AlloyDB      — async, fire-and-forget to trust_ledger table (system of record)
  3. Cloud Logging — async, fire-and-forget via observability (Layer 8)
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

# Map internal entry_type labels → AlloyDB event_type enum
_EVENT_TYPE_MAP = {
    "gate":        "ESCALATION_PENDING",
    "decision":    "REPORTED_DECISION",
    "escalation":  "ESCALATION_PENDING",
    "resolved":    "ESCALATION_RESOLVED",
    "audit":       "AUDIT_FINDING",
}


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


# ── AlloyDB trust_ledger insert (async, called via fire_and_forget) ───────────

async def _db_insert_trust_ledger(
    tenant_id: str,
    event_type: str,
    decision_class: str,
    agent_id: str,
    detail: str,
) -> None:
    try:
        from .db import get_pool
        pool = await get_pool()
        if pool is None:
            return
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO trust_ledger
                  (tenant_id, swarm_id, role_category, event_type, decision_class, evidence)
                VALUES ($1, $2, $3, $4, $5, $6)
                """,
                tenant_id,
                "pmo-swarm",
                agent_id,
                event_type,
                decision_class,
                json.dumps({"detail": detail[:500]}),
            )
    except Exception as e:
        # AlloyDB write failure is non-fatal — local JSONL is the backup
        import logging
        logging.getLogger(__name__).warning(
            f"trust_ledger AlloyDB write failed ({e}) — local JSONL preserved"
        )


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

    # 2. AlloyDB trust_ledger table — async fire-and-forget
    try:
        from .config_registry import get_tenant_id, get_decision_class
        from .db import fire_and_forget
        tenant_id      = get_tenant_id()
        decision_class = get_decision_class()
        db_event_type  = _EVENT_TYPE_MAP.get(entry_type, "REPORTED_DECISION")
        fire_and_forget(
            _db_insert_trust_ledger(tenant_id, db_event_type, decision_class, agent_id, detail)
        )
    except Exception:
        pass  # never block the caller

    # 3. BigQuery trust_events — async fire-and-forget (analytics)
    try:
        from .analytics import log_trust_event
        fire_and_forget(log_trust_event(entry_type, detail, agent_id=agent_id))
    except Exception:
        pass

    # 4. Cloud Logging — async fire-and-forget (Layer 8)
    _obs_log(entry_type, detail, agent_id=agent_id)


def trust_ledger_read(last_n: int = 50) -> list:
    """Read from local JSONL (fast path). AlloyDB is the durable store for reporting."""
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
    """Read trust ledger from AlloyDB — used for reporting and the Operating Brief."""
    try:
        from .db import get_pool
        from .config_registry import get_tenant_id
        pool = await get_pool()
        if pool is None:
            return trust_ledger_read(last_n)
        if not tenant_id:
            tenant_id = get_tenant_id()
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role_category, event_type, decision_class, evidence, created_at
                FROM trust_ledger
                WHERE swarm_id = 'pmo-swarm'
                ORDER BY created_at DESC
                LIMIT $1
                """,
                last_n,
            )
        return [dict(r) for r in rows]
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"trust_ledger DB read failed ({e}) — using local JSONL")
        return trust_ledger_read(last_n)

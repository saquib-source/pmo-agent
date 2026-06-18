"""
ISRDS Governance Middleware (Layer 5 + Layer 6)
Reads governance-rules.yaml at runtime and enforces authority ceiling + Trust Ledger.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

AGENT_DIR = Path(__file__).parent.parent
GOV_PATH = AGENT_DIR / "governance-rules.yaml"

try:
    import yaml
    GOVERNANCE = yaml.safe_load(GOV_PATH.read_text()) if GOV_PATH.exists() else {}
except ImportError:
    GOVERNANCE = {}


def governance_check(action: str, is_irreversible: bool = False) -> dict:
    """Check if an action is allowed under governance rules."""
    rules = GOVERNANCE.get("rules", [])
    for rule in rules:
        if rule.get("action") == action:
            decision = rule.get("decision", "allow")
            if decision == "deny":
                return {"allowed": False, "gate": None, "reason": rule.get("rationale")}
            if decision == "gate":
                return {"allowed": False, "gate": rule.get("gate"), "reason": rule.get("rationale")}
    if is_irreversible:
        return {"allowed": False, "gate": "Approve", "reason": "Irreversible → Approve"}
    return {"allowed": True, "gate": None, "reason": "Allowed"}


# ── Trust Ledger (FR-7) ──
LEDGER_FILE = os.environ.get("TRUST_LEDGER_PATH", str(AGENT_DIR / "trust-ledger.jsonl"))


def trust_ledger_log(entry_type: str, detail: str) -> None:
    """Append to Trust Ledger. Append-only, never overwrite."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": entry_type,
        "detail": detail,
        "agent_id": "pmo_execution_agent",
    }
    with open(LEDGER_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def trust_ledger_read(last_n: int = 50) -> list[dict]:
    """Read last N ledger entries."""
    if not os.path.exists(LEDGER_FILE):
        return []
    with open(LEDGER_FILE) as f:
        lines = f.readlines()
    return [json.loads(line) for line in lines[-last_n:]]

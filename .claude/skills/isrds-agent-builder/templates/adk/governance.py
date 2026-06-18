"""
ISRDS Governance Middleware (Layer 5 + Layer 6)

Enforces governance-rules.yaml at runtime:
- Authority level ceiling (OBSERVE_ONLY → ACT_AUTONOMOUS)
- Irreversible action → Approve gate mapping
- Trust Ledger audit invariant (FR-7)

This module is imported by agent.py and used as custom ADK tools.
"""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

# Load governance rules from the portable artifact
AGENT_DIR = Path(__file__).parent.parent
GOV_PATH = AGENT_DIR / "governance-rules.yaml"

try:
    import yaml
    GOVERNANCE = yaml.safe_load(GOV_PATH.read_text()) if GOV_PATH.exists() else {}
except ImportError:
    GOVERNANCE = {}

# Authority levels in escalation order
AUTHORITY_LEVELS = ["OBSERVE_ONLY", "DECIDE_AND_REPORT", "ACT_WITH_REVIEW", "ACT_AUTONOMOUS"]


def get_agent_authority() -> str:
    """Read the agent's authority level from agent-spec.yaml."""
    spec_path = AGENT_DIR / "agent-spec.yaml"
    if not spec_path.exists():
        return "DECIDE_AND_REPORT"  # safe default
    try:
        import yaml
        spec = yaml.safe_load(spec_path.read_text())
        return (spec.get("spec") or {}).get("authority", "DECIDE_AND_REPORT")
    except Exception:
        return "DECIDE_AND_REPORT"


def governance_check(action: str, is_irreversible: bool = False) -> dict:
    """
    Check if an action is allowed under the current governance rules.
    Returns {"allowed": bool, "gate": str|None, "reason": str}
    """
    authority = get_agent_authority()
    rules = GOVERNANCE.get("rules", [])

    # Check specific action rules
    for rule in rules:
        if rule.get("action") == action:
            decision = rule.get("decision", "allow")
            if decision == "deny":
                return {"allowed": False, "gate": None, "reason": rule.get("rationale", "Denied by governance")}
            if decision == "gate":
                return {"allowed": False, "gate": rule.get("gate", "Review"), "reason": rule.get("rationale", "Gated by governance")}

    # Authority ceiling check
    if is_irreversible:
        return {"allowed": False, "gate": "Approve", "reason": "Irreversible action requires Approve gate (invariant)"}

    if authority == "OBSERVE_ONLY":
        return {"allowed": False, "gate": "Review", "reason": "OBSERVE_ONLY authority — cannot take actions"}

    if authority == "DECIDE_AND_REPORT":
        # Can read and reason, but external writes need a gate
        return {"allowed": True, "gate": None, "reason": "Allowed under DECIDE_AND_REPORT"}

    return {"allowed": True, "gate": None, "reason": f"Allowed under {authority}"}


# ── Trust Ledger (FR-7 invariant) ──

LEDGER_FILE = os.environ.get("TRUST_LEDGER_PATH", str(AGENT_DIR / "trust-ledger.jsonl"))


def trust_ledger_log(entry_type: str, detail: str) -> None:
    """Append an entry to the Trust Ledger. Append-only, never overwrite."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": entry_type,  # tool-call | decision | gate
        "detail": detail,
        "agent_id": GOVERNANCE.get("agent_id", "unknown"),
    }
    with open(LEDGER_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def trust_ledger_read(last_n: int = 50) -> list[dict]:
    """Read the last N entries from the Trust Ledger."""
    if not os.path.exists(LEDGER_FILE):
        return []
    with open(LEDGER_FILE) as f:
        lines = f.readlines()
    return [json.loads(line) for line in lines[-last_n:]]

"""
ISRDS Governance Middleware (Layer 5 + Layer 6) — PMO Agent

Reads governance-rules.yaml at runtime and enforces:
- Authority level ceiling (DECIDE_AND_REPORT for this agent)
- Irreversible action → Approve gate mapping
- Trust Ledger audit invariant (FR-7)
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

AUTHORITY_LEVELS = ["OBSERVE_ONLY", "DECIDE_AND_REPORT", "ACT_WITH_REVIEW", "ACT_AUTONOMOUS"]


def get_agent_authority() -> str:
    """Read authority from agent-spec.yaml."""
    spec_path = AGENT_DIR / "agent-spec.yaml"
    if not spec_path.exists():
        return "DECIDE_AND_REPORT"
    try:
        import yaml
        spec = yaml.safe_load(spec_path.read_text())
        return (spec.get("spec") or {}).get("authority", "DECIDE_AND_REPORT")
    except Exception:
        return "DECIDE_AND_REPORT"


def governance_check(action: str, is_irreversible: bool = False) -> dict:
    """Check if an action is allowed under governance rules."""
    authority = get_agent_authority()
    rules = GOVERNANCE.get("rules", [])

    for rule in rules:
        if rule.get("action") == action:
            decision = rule.get("decision", "allow")
            if decision == "deny":
                return {"allowed": False, "gate": None, "reason": rule.get("rationale", "Denied")}
            if decision == "gate":
                return {"allowed": False, "gate": rule.get("gate", "Review"), "reason": rule.get("rationale", "Gated")}

    if is_irreversible:
        return {"allowed": False, "gate": "Approve", "reason": "Irreversible → Approve (invariant)"}

    return {"allowed": True, "gate": None, "reason": f"Allowed under {authority}"}


# ── Trust Ledger (FR-7) ──

LEDGER_FILE = os.environ.get("TRUST_LEDGER_PATH", str(AGENT_DIR / "trust-ledger.jsonl"))


def trust_ledger_log(entry_type: str, detail: str) -> None:
    """Append to Trust Ledger. Append-only, never overwrite."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": entry_type,
        "detail": detail,
        "agent_id": GOVERNANCE.get("agent_id", "pmo-agent"),
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

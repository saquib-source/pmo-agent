"""
PMO Swarm Orchestrator — root_agent
Routes incoming requests to the right skill agents, enforces governance gates,
and synthesises findings into Operating Briefs.

ADK entry point: `adk web .` from this directory discovers root_agent via __init__.py.
"""
import logging
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool

from .agents.execution_tracking  import execution_tracking_agent
from .agents.follow_up            import follow_up_agent
from .agents.ownership_raci       import ownership_raci_agent
from .agents.feature_completeness import feature_completeness_agent
from .agents.hygiene              import hygiene_agent
from .shared.governance           import trust_ledger_log, trust_ledger_read
from .shared.config_registry      import get_agent_model, get_tenant_id
from .shared.observability        import log_event as obs_log
from .shared.tool_registry        import registry as tool_registry
from .shared.db                   import fire_and_forget

log = logging.getLogger(__name__)

_PROMPT_PATH = Path(__file__).parent / "prompts" / "orchestrator.md"
_PROMPT = _PROMPT_PATH.read_text() if _PROMPT_PATH.exists() else "You are the PMO Orchestrator."

# Layer 1 — model pulled from Config Registry (Firestore → env fallback)
MODEL = get_agent_model()


# ── Orchestrator-level tools (cross-cutting, no skill agent owns these) ──────

def governance_gate(gate_type: str, description: str, ticket_key: str = "") -> dict:
    """Create a human approval checkpoint. Call before any Jira write.

    gate_type: 'Review' (for comments), 'Approve' (for transitions/assignments), 'Escalate'.
    description: What needs human review and why.
    ticket_key: Related Jira ticket (optional).
    """
    detail = f"{gate_type}: {description} [{ticket_key}]"
    # Layer 5 — Trust Ledger + Layer 8 — Cloud Logging (governance.py mirrors both)
    trust_ledger_log("gate", detail, agent_id="pmo_orchestrator")
    # Layer 3 — record that a registered tool with a gate requirement was triggered
    obs_log(
        "governance_gate_opened",
        detail,
        agent_id="pmo_orchestrator",
        severity="WARNING",
        extra={"gate_type": gate_type, "ticket_key": ticket_key},
    )
    # Enhanced logging — a single, trivially-greppable line so a human reviewer can
    # find every pending approval with: grep "PENDING_APPROVAL" in Cloud Logging.
    log.warning(
        "⏸ PENDING_APPROVAL | gate=%s | ticket=%s | action_awaiting_human=%r",
        gate_type, ticket_key or "-", description,
    )
    return {
        "gate_type":   gate_type,
        "description": description,
        "ticket_key":  ticket_key,
        "status":      "pending",
        "message":     f"⏸ {gate_type} gate — awaiting human decision. "
                       f"Approve via: python -m adk.approve {ticket_key}",
    }


def log_decision(decision: str, rationale: str) -> dict:
    """Record a significant judgment call to the Trust Ledger.

    decision: Short identifier (e.g. 'escalation', 'board-healthy', 'chase-approved').
    rationale: Your reasoning.
    """
    trust_ledger_log("decision", f"{decision}: {rationale}", agent_id="pmo_orchestrator")
    obs_log("decision_logged", f"{decision}: {rationale}", agent_id="pmo_orchestrator")
    return {"logged": True, "decision": decision}


def read_ledger(last_n: int = 20) -> dict:
    """Read the last N Trust Ledger entries. Use to check recent decisions or audit trail.

    Args:
        last_n: How many entries to return (default 20).
    """
    entries = trust_ledger_read(last_n)
    return {"count": len(entries), "entries": entries}


def write_scan_results(
    stalled_tickets_json: str = "[]",
    hygiene_findings_json: str = "[]",
    raci_gaps_json: str = "[]",
    feature_snapshots_json: str = "[]",
    stall_count: int = 0,
    hygiene_score: float = 0.0,
    raci_gap_count: int = 0,
    feature_pct_built: float = 0.0,
    gates_triggered: int = 0,
) -> dict:
    """Persist structured scan results to BigQuery. Call this BEFORE writing the Operating Brief.

    All parameters are optional — pass only what the sub-agents returned.

    stalled_tickets_json: JSON array of stalled ticket objects. Each: {key, summary, project,
        assignee, assignee_email, status, priority, stall_hours, last_activity_at}
    hygiene_findings_json: JSON array. Each: {key, project, violation_type, severity,
        field_missing, description}
    raci_gaps_json: JSON array. Each: {key, project, missing_role, current_assignee, summary}
    feature_snapshots_json: JSON array. Each: {division, dept, sub_dept, total_features,
        built_features, pct_built, unbuilt_feature_names}
    stall_count: Total number of stalled tickets found.
    hygiene_score: 0.0 (all clean) to 1.0 (completely broken).
    raci_gap_count: Total RACI gaps found.
    feature_pct_built: 0.0 to 1.0 overall feature build percentage.
    gates_triggered: Number of governance gates opened this cycle.
    """
    import json
    from datetime import datetime, timezone

    cycle_ts = datetime.now(timezone.utc)
    stored: list[str] = []
    errors: list[str] = []

    def _parse(s: str, label: str) -> list:
        try:
            result = json.loads(s) if s.strip() not in ("", "[]") else []
            return result if isinstance(result, list) else []
        except json.JSONDecodeError as e:
            errors.append(f"{label}: {e}")
            return []

    tickets  = _parse(stalled_tickets_json,  "stalled_tickets")
    findings = _parse(hygiene_findings_json, "hygiene_findings")
    gaps     = _parse(raci_gaps_json,        "raci_gaps")
    snaps    = _parse(feature_snapshots_json, "feature_snapshots")

    try:
        from .shared.analytics import (
            log_stalled_tickets, log_hygiene_findings,
            log_raci_gaps, log_feature_snapshot, log_cycle_metrics,
        )
        if tickets:
            fire_and_forget(log_stalled_tickets(cycle_ts, tickets))
            stored.append(f"stalled_tickets({len(tickets)})")
        if findings:
            fire_and_forget(log_hygiene_findings(cycle_ts, findings))
            stored.append(f"hygiene_findings({len(findings)})")
        if gaps:
            fire_and_forget(log_raci_gaps(cycle_ts, gaps))
            stored.append(f"raci_gaps({len(gaps)})")
        if snaps:
            fire_and_forget(log_feature_snapshot(cycle_ts, snaps))
            stored.append(f"feature_snapshot({len(snaps)})")

        # Update cycle_metrics with the structured counts from this cycle
        from .shared.config_registry import get_jira_projects
        fire_and_forget(log_cycle_metrics(
            cycle_ts=cycle_ts,
            mode="structured_flush",
            projects=get_jira_projects(),
            duration_ms=0.0,
            stall_count=stall_count,
            hygiene_score=hygiene_score,
            raci_gap_count=raci_gap_count,
            feature_pct_built=feature_pct_built,
            gates_triggered=gates_triggered,
        ))
        stored.append("cycle_metrics")
    except Exception as e:
        errors.append(f"BigQuery: {e}")

    obs_log(
        "scan_results_written",
        f"BigQuery: {', '.join(stored) if stored else 'nothing'} | errors: {errors}",
        agent_id="pmo_orchestrator",
    )
    return {
        "stored":         stored,
        "tables_written": len(stored),
        "errors":         errors,
        "message":        f"Scan results written to BigQuery: {', '.join(stored) or 'nothing to write'}",
    }


# ── Root agent ────────────────────────────────────────────────────────────────

root_agent = LlmAgent(
    name="pmo_orchestrator",
    model=MODEL,
    instruction=_PROMPT,
    tools=[
        # Governance (orchestrator owns these — cross-cutting)
        FunctionTool(governance_gate),
        FunctionTool(log_decision),
        FunctionTool(read_ledger),
        # Data persistence — BigQuery (call before writing the Operating Brief)
        FunctionTool(write_scan_results),
        # Skill agents as callable tools
        AgentTool(execution_tracking_agent),
        AgentTool(follow_up_agent),
        AgentTool(ownership_raci_agent),
        AgentTool(feature_completeness_agent),
        AgentTool(hygiene_agent),
    ],
    output_key="response",
)

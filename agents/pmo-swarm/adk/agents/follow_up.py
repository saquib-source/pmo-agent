"""
Follow-up & Escalation Agent — persona: Danielle, PMO Execution Lead.
Handles all Jira WRITE actions (comments, transitions). Every write goes through a
governance gate before execution. MUST_ESCALATE for transitions; DECIDE_AND_REPORT for comments.

Inter-agent wiring:
  ← called by: execution_tracking_agent (stalled tickets), hygiene_agent (violations)
  → calls:     ownership_raci_agent (ALWAYS before drafting — needs Accountable/Responsible to @mention correctly)
"""
import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool

from ..shared import jira_client as jc
from ..shared.governance import trust_ledger_log, governance_check, queue_pending_action
from .ownership_raci import ownership_raci_agent

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "follow_up.md"
_PROMPT = _PROMPT_PATH.read_text() if _PROMPT_PATH.exists() else "You are Danielle, PMO Execution Lead."
MODEL = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")


# ── Tools ────────────────────────────────────────────────────────────────────

def draft_followup_ping(
    ticket_key: str, assignee: str, stalled_hours: int,
    is_critical: bool, message: str,
) -> dict:
    """Draft a chase message for a stalled ticket owner. Does NOT post — goes through governance gate.

    Write the message as Danielle: warm, direct, professional. Reference specific ticket details.
    Do NOT start with 'Hi [name]' — the system prepends the @mention.
    Do NOT include a sign-off — the system appends '— Danielle, PMO Execution Lead'.

    Args:
        ticket_key: Jira ticket key (e.g. ISRDS-1510).
        assignee: The person to chase (display name).
        stalled_hours: How long the ticket has been idle.
        is_critical: True if this ticket is on the critical path.
        message: The chase body (2-3 paragraphs, no greeting, no sign-off).
    """
    urgency = "escalation" if stalled_hours >= 72 else "chase" if stalled_hours >= 48 else "reminder"
    trust_ledger_log(
        "draft-ping",
        f"Drafted {urgency} for {ticket_key} → {assignee} ({stalled_hours}h)",
        agent_id="pmo_follow_up",
    )
    # Persist the REAL, human-voiced message for human approval. The UI posts this
    # `message` verbatim — so what lands on Jira is Danielle's actual note, not a
    # meta description of what was drafted.
    queue_pending_action(
        agent_id="pmo_follow_up",
        action_type="comment",
        ticket_key=ticket_key,
        message=message,
        assignee_name=assignee,
        urgency=urgency,
    )
    return {
        "ticket_key":    ticket_key,
        "assignee":      assignee,
        "message":       message,
        "urgency":       urgency,
        "stalled_hours": stalled_hours,
        "is_critical":   is_critical,
        "status":        "drafted — pending governance gate",
    }


def post_comment(ticket_key: str, message: str, assignee_name: str = "",
                 assignee_id: str = "") -> dict:
    """Post a comment on a Jira ticket. Only call AFTER the orchestrator has approved a Review gate.

    Args:
        ticket_key: Jira ticket key.
        message: Comment body (no greeting, no sign-off — system adds them).
        assignee_name: Display name for the @mention (optional).
        assignee_id: Jira accountId for the @mention (optional).
    """
    check = governance_check("jira-comment")
    if not check["allowed"] and check.get("gate"):
        return {
            "blocked": True,
            "gate":    check["gate"],
            "reason":  check["reason"],
            "message": f"Comment blocked — needs {check['gate']} gate.",
        }
    return jc.add_comment_adf(ticket_key, message, assignee_name or None, assignee_id or None)


def request_transition(issue_key: str, transition_name: str) -> dict:
    """Request a workflow status change (e.g. 'To Do' → 'In Progress').
    Always requires an Approve gate from the orchestrator before this is called.

    Args:
        issue_key: Jira ticket key.
        transition_name: Target status name.
    """
    check = governance_check("jira-transition", is_irreversible=True)
    if not check["allowed"]:
        return {
            "blocked": True,
            "gate":    check.get("gate", "Approve"),
            "reason":  check["reason"],
            "message": f"Transition blocked — needs {check.get('gate','Approve')} gate.",
        }
    return jc.transition_issue(issue_key, transition_name, agent_id="pmo_follow_up")


def escalate(ticket_key: str, assignee: str, hours_stalled: int, rationale: str) -> dict:
    """Flag a ticket for leadership escalation. Logs to Trust Ledger and returns escalation record.
    The orchestrator surfaces this in the Operating Brief.

    Args:
        ticket_key: Jira ticket key.
        assignee: Current owner.
        hours_stalled: How long it's been idle.
        rationale: Why this warrants escalation.
    """
    trust_ledger_log(
        "escalation",
        f"{ticket_key} → {assignee} | {hours_stalled}h | {rationale}",
        agent_id="pmo_follow_up",
    )
    return {
        "escalated":     True,
        "ticket_key":    ticket_key,
        "assignee":      assignee,
        "hours_stalled": hours_stalled,
        "rationale":     rationale,
        "status":        "logged — surfaced in Operating Brief",
    }


# ── Agent ────────────────────────────────────────────────────────────────────

follow_up_agent = LlmAgent(
    name="follow_up_agent",
    model=MODEL,
    instruction=_PROMPT,
    tools=[
        FunctionTool(draft_followup_ping),
        FunctionTool(post_comment),
        FunctionTool(request_transition),
        FunctionTool(escalate),
        # Pull RACI for every ticket before drafting — who is Accountable, Responsible?
        AgentTool(ownership_raci_agent),
    ],
    output_key="response",
)

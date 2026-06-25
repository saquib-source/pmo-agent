"""
Follow-up & Escalation Agent — persona: Danielle, PMO Execution Lead.
Handles all Jira WRITE actions (comments, transitions). Every write goes through a
governance gate before execution. MUST_ESCALATE for transitions; DECIDE_AND_REPORT for comments.

Inter-agent wiring:
  ← called by: execution_tracking_agent (stalled tickets), hygiene_agent (violations)
  → calls:     ownership_raci_agent (ALWAYS before drafting — needs Accountable/Responsible to @mention correctly)
"""
import asyncio
import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool

from ..shared import jira_client as jc
from ..shared import conversation as convo
from ..shared.decision import should_comment
from ..shared.governance import trust_ledger_log, governance_check, queue_pending_action
from .ownership_raci import ownership_raci_agent


def _run_async(coro):
    """Run an async helper from ADK's sync tool context.

    ADK invokes tools from inside a running event loop, so asyncio.run() / nested
    loops are illegal. Run the coroutine to completion in a dedicated worker thread
    with its own loop, and block this (sync) tool thread until it finishes.
    """
    import concurrent.futures

    def _worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    try:
        asyncio.get_running_loop()          # are we inside a live loop?
    except RuntimeError:
        return asyncio.run(coro)            # no — simplest path (CLI/tests)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_worker).result()

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "follow_up.md"
_PROMPT = _PROMPT_PATH.read_text() if _PROMPT_PATH.exists() else "You are Danielle, PMO Execution Lead."
MODEL = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")


# ── Tools ────────────────────────────────────────────────────────────────────

def assess_ticket(ticket_key: str, ask_kind: str = "stall_chase",
                  subject_terms: str = "") -> dict:
    """ALWAYS call this FIRST, before drafting anything for a ticket.

    Reads the live comment thread + Danielle's memory of past interactions and tells
    you whether to comment at all and how. Prevents re-nagging and unnecessary updates.

    Args:
        ticket_key: Jira ticket key.
        ask_kind: the kind of ask you're considering — 'stall_chase', 'hygiene_fields',
                  'blocker', 'status_request'. Used to recognise a repeat ask.
        subject_terms: comma-separated things being asked about (e.g. 'epic,estimate,duedate').
                       Used so a re-ask of the same fields is detected as already-answered.

    Returns a verdict:
        act:   True if you may comment this cycle, False if you must stay silent.
        mode:  'interpret_reply' → a human replied, read it and resolve (close/escalate);
               'new_followup'    → draft a fresh chase via draft_followup_ping;
               'skip'            → do nothing, call note_no_comment_needed.
        reason, thread_state, last_human_reply, history.
    """
    detail = jc.get_issue_detail(ticket_key)
    comments = detail.get("all_comments") or detail.get("comments") or []
    terms = [t.strip() for t in subject_terms.split(",") if t.strip()]
    intent = convo.intent_hash(ticket_key, ask_kind, terms)
    verdict = _run_async(should_comment(ticket_key, comments, intent))
    verdict["intent_hash"] = intent
    trust_ledger_log("assess", f"{ticket_key}: {verdict['mode']} — {verdict['reason']}",
                     agent_id="pmo_follow_up")
    return verdict


def note_no_comment_needed(ticket_key: str, reason: str) -> dict:
    """Record that Danielle deliberately chose NOT to comment on a ticket this cycle.
    Call this whenever assess_ticket returns act=False, or whenever your own judgement
    is that a comment would be noise. This is how Danielle 'learns' restraint over time.

    Args:
        ticket_key: Jira ticket key.
        reason: why no comment is needed (e.g. 'assignee already answered yesterday').
    """
    convo.record_event(ticket_key, "pmo_silent", body=reason, decision="stay_silent",
                       actor="Danielle")
    trust_ledger_log("no-comment", f"{ticket_key}: stayed silent — {reason}",
                     agent_id="pmo_follow_up")
    return {"ticket_key": ticket_key, "decision": "stay_silent", "reason": reason}


def interpret_and_resolve(ticket_key: str, human_reply: str, interpretation: str,
                          resolution: str, message: str = "",
                          accountable_name: str = "", accountable_id: str = "") -> dict:
    """Handle a human's reply to one of Danielle's asks (the assess_ticket 'interpret_reply' mode).

    Use this INSTEAD of re-chasing when a human has already responded. Decide one of:
      - 'close_loop': the reply resolves the ask — acknowledge briefly and stop.
      - 'escalate':   the reply shows the assignee cannot resolve it (e.g. "not sure what
                      the parent epic is", "ongoing, no due date") — route the OPEN DECISION
                      to the Accountable owner. Pass message + accountable_* to @mention them.
      - 'noted':      reply needs no Jira comment; just record the understanding.

    Args:
        ticket_key: Jira ticket key.
        human_reply: the human's reply text you are responding to.
        interpretation: your plain-language read of what the reply means.
        resolution: one of 'close_loop' | 'escalate' | 'noted'.
        message: the comment body to post (no greeting/sign-off). Required for close_loop/escalate.
        accountable_name / accountable_id: the Accountable owner to @mention when escalating.
    """
    convo.record_event(ticket_key, "human_reply", body=human_reply, actor="human")
    convo.record_event(ticket_key, "pmo_interpret", interpretation=interpretation,
                       decision=resolution, actor="Danielle")

    if resolution == "noted":
        trust_ledger_log("interpret", f"{ticket_key}: noted — {interpretation[:120]}",
                         agent_id="pmo_follow_up")
        return {"ticket_key": ticket_key, "resolution": "noted",
                "interpretation": interpretation, "status": "recorded, no comment posted"}

    if not message:
        return {"error": "message is required for close_loop and escalate resolutions"}

    event = "pmo_escalate" if resolution == "escalate" else "pmo_close_loop"
    mention_name = accountable_name if resolution == "escalate" else ""
    mention_id   = accountable_id   if resolution == "escalate" else ""

    queue_pending_action(
        agent_id="pmo_follow_up", action_type="comment", ticket_key=ticket_key,
        message=message, assignee_name=mention_name, assignee_id=mention_id,
        urgency="escalation" if resolution == "escalate" else "close_loop",
    )
    convo.record_event(ticket_key, event, body=message, decision=resolution, actor="Danielle")
    trust_ledger_log("interpret", f"{ticket_key}: {resolution} — {interpretation[:100]}",
                     agent_id="pmo_follow_up")
    return {"ticket_key": ticket_key, "resolution": resolution,
            "interpretation": interpretation, "message": message,
            "status": "queued — pending human approval"}


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
    # Hard eligibility gate — never chase Done/To Do or backlog tickets, regardless
    # of how this ticket reached us. Applies whether or not a human later approves.
    elig = jc.is_comment_eligible(ticket_key)
    if not elig["eligible"]:
        trust_ledger_log(
            "skip-followup",
            f"Skipped {ticket_key} → {assignee}: {elig['reason']}",
            agent_id="pmo_follow_up",
        )
        return {
            "ticket_key": ticket_key,
            "assignee":   assignee,
            "skipped":    True,
            "reason":     elig["reason"],
            "status":     f"not eligible for follow-up — {elig['reason']}",
        }

    urgency = "escalation" if stalled_hours >= 72 else "chase" if stalled_hours >= 48 else "reminder"
    trust_ledger_log(
        "draft-ping",
        f"Drafted {urgency} for {ticket_key} → {assignee} ({stalled_hours}h)",
        agent_id="pmo_follow_up",
    )
    # Remember this ask so future cycles recognise it and don't re-nag once answered.
    convo.record_event(
        ticket_key, "pmo_ask", body=message, actor="Danielle",
        intent_hash=convo.intent_hash(ticket_key, urgency, [message[:40]]),
        metadata={"assignee": assignee, "stalled_hours": stalled_hours, "urgency": urgency},
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
        FunctionTool(assess_ticket),
        FunctionTool(note_no_comment_needed),
        FunctionTool(interpret_and_resolve),
        FunctionTool(draft_followup_ping),
        FunctionTool(post_comment),
        FunctionTool(request_transition),
        FunctionTool(escalate),
        # Pull RACI for every ticket before drafting — who is Accountable, Responsible?
        AgentTool(ownership_raci_agent),
    ],
    output_key="response",
)

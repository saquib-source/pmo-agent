"""
The 'should I comment?' gate — Danielle's restraint logic.

Combines durable memory (ticket_interactions) with deterministic rules to keep
Danielle from posting unnecessary updates, BEFORE any LLM drafting happens. The
LLM still makes the final judgement call on *what* to say; this gate decides
whether she should speak at all and surfaces the context she needs to decide well.

Policy knobs (env, with sensible defaults):
  PMO_CONTACT_COOLDOWN_HOURS   min hours between PMO comments on one ticket (default 24)
  PMO_REPLY_GRACE_HOURS        if a human replied this recently, interpret — don't chase (default 0 → always interpret)
"""
import os

from . import conversation as convo


def _cooldown_hours() -> float:
    try:
        return float(os.environ.get("PMO_CONTACT_COOLDOWN_HOURS", "24"))
    except ValueError:
        return 24.0


async def should_comment(ticket_key: str, comments: list, intent: str = "") -> dict:
    """Decide whether Danielle should comment on this ticket right now.

    Returns:
      {
        "act": bool,                  # may she write a Jira comment this cycle?
        "mode": str,                  # 'interpret_reply' | 'new_followup' | 'skip'
        "reason": str,                # human-readable rationale (also logged)
        "thread_state": str,          # from classify_thread
        "last_human_reply": dict|None,# the reply to interpret, if any
        "history": list,              # prior interaction timeline (for the LLM)
      }
    """
    thread = convo.classify_thread(comments)
    state  = thread["state"]
    history = await convo.recall_interactions(ticket_key)

    base = {
        "thread_state":     state,
        "last_human_reply": thread["last_human_reply"],
        "history":          history,
    }

    # 1. A human replied after Danielle's last comment → her job is to INTERPRET,
    #    never to re-chase. This is the Meri case.
    if state == "human_replied":
        return {**base, "act": True, "mode": "interpret_reply",
                "reason": "human replied after PMO ask — read and decide (close loop / escalate)"}

    # 2. Already-answered ask → stay silent. Memory beats re-nagging.
    if intent and await convo.ask_already_answered(ticket_key, intent):
        return {**base, "act": False, "mode": "skip",
                "reason": "this exact ask was already raised and answered — no re-ask"}

    # 3. Cooldown — don't pile comments on a ticket Danielle just contacted.
    since = convo.hours_since_last_pmo_comment(comments)
    cooldown = _cooldown_hours()
    if since < cooldown:
        return {**base, "act": False, "mode": "skip",
                "reason": f"contacted {since:.0f}h ago (<{cooldown:.0f}h cooldown) — too soon to comment again"}

    # 4. Danielle is waiting on someone and the cooldown has passed → a fresh
    #    follow-up is reasonable (the LLM decides if escalation tone is warranted).
    if state == "awaiting_them":
        return {**base, "act": True, "mode": "new_followup",
                "reason": f"awaiting response for {since:.0f}h, past cooldown — a follow-up is due"}

    # 5. Quiet / human-initiated with no PMO history → only a genuinely new ask
    #    justifies breaking silence. Default to a measured follow-up.
    if state in ("quiet", "human_initiated"):
        return {**base, "act": True, "mode": "new_followup",
                "reason": f"no recent PMO contact ({state}) — an initial follow-up is appropriate"}

    return {**base, "act": False, "mode": "skip", "reason": "no condition warrants a comment"}

"""
Conversation awareness for the PMO swarm.

Two responsibilities:
  1. Classify a ticket's comment thread (whose turn is it?), so Danielle can tell
     "nobody answered my chase" from "a human already replied — read it".
  2. Persist and recall the per-ticket interaction timeline (ticket_interactions),
     giving her a human-like memory of what she asked and how it was answered.

The agent's own comments are recognised by the sign-off the Jira client appends:
"— Delivery Agent". This is the contract — see jira_client.add_comment_adf.
Comments posted before the 2026-07-14 rename carry the legacy sign-off
"— Danielle, PMO Execution Lead" and must still be recognised as ours.
"""
import hashlib
import logging
import re

from . import jira_client as jc
from .db import get_pool, fire_and_forget

log = logging.getLogger(__name__)

PMO_SIGNATURE = "— Delivery Agent"
_LEGACY_SIGNATURES = ("danielle, pmo execution lead",)


# ── Thread classification ─────────────────────────────────────────────────────

def _is_pmo_comment(body: str) -> bool:
    text = (body or "").lower()
    if PMO_SIGNATURE.lower() in text:
        return True
    return any(sig in text for sig in _LEGACY_SIGNATURES)


def classify_thread(comments: list) -> dict:
    """Given an ordered list of comment dicts ({author, created, body}), determine
    the conversation state from Danielle's point of view.

    Returns:
      state: 'awaiting_them' | 'human_replied' | 'human_initiated' | 'quiet'
      last_pmo_idx / last_human_idx: indices into `comments` (or -1)
      last_human_reply: the human comment dict that needs interpretation, or None
      pmo_has_spoken: bool
    """
    last_pmo_idx = last_human_idx = -1
    for i, c in enumerate(comments or []):
        if _is_pmo_comment(c.get("body", "")):
            last_pmo_idx = i
        else:
            last_human_idx = i

    pmo_has_spoken = last_pmo_idx >= 0
    if not comments:
        state = "quiet"
    elif not pmo_has_spoken:
        # Humans talking, Danielle never engaged. Only her business if a human asks.
        state = "human_initiated" if last_human_idx >= 0 else "quiet"
    elif last_human_idx > last_pmo_idx:
        # A human spoke *after* Danielle's last comment → ball is in her court.
        state = "human_replied"
    else:
        # Danielle spoke last; nobody has answered yet.
        state = "awaiting_them"

    return {
        "state":            state,
        "last_pmo_idx":     last_pmo_idx,
        "last_human_idx":   last_human_idx,
        "pmo_has_spoken":   pmo_has_spoken,
        "last_human_reply": comments[last_human_idx] if last_human_idx >= 0 else None,
    }


def hours_since_last_pmo_comment(comments: list) -> float:
    """Hours since Danielle last commented; large number if she never has."""
    for c in reversed(comments or []):
        if _is_pmo_comment(c.get("body", "")):
            return jc._hours_since(c.get("created", "")) or 0.0
    return 1e9


# ── Intent hashing ────────────────────────────────────────────────────────────

_WORD = re.compile(r"[a-z0-9]+")

def intent_hash(ticket_key: str, ask_kind: str, subject_terms: list = None) -> str:
    """Stable id for the *intent* of an ask, so a re-ask of the same thing is
    recognisable regardless of wording.

    ask_kind: short tag like 'hygiene_fields', 'stall_chase', 'blocker'.
    subject_terms: the things being asked about (e.g. ['epic','estimate','duedate']).
    """
    terms = sorted({t for term in (subject_terms or []) for t in _WORD.findall(term.lower())})
    raw = f"{ticket_key}|{ask_kind}|{','.join(terms)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


# ── Interaction memory (ticket_interactions) ──────────────────────────────────

_TICKET_INTERACTIONS_DDL = [
    """
    CREATE TABLE IF NOT EXISTS ticket_interactions (
      id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
      tenant_id       TEXT        NOT NULL,
      swarm_id        TEXT        NOT NULL DEFAULT 'pmo-swarm',
      ticket_key      TEXT        NOT NULL,
      event_type      TEXT        NOT NULL,
      intent_hash     TEXT,
      actor           TEXT,
      actor_id        TEXT,
      body            TEXT,
      interpretation  TEXT,
      decision        TEXT,
      jira_comment_id TEXT,
      metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
      created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ticket_interactions_ticket "
    "ON ticket_interactions (tenant_id, ticket_key, created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_ticket_interactions_intent "
    "ON ticket_interactions (tenant_id, ticket_key, intent_hash)",
]


async def _ensure_table() -> None:
    from .db import ensure_schema_once
    await ensure_schema_once("ticket_interactions", _TICKET_INTERACTIONS_DDL)


async def _insert(tenant_id: str, ticket_key: str, event_type: str, **kw) -> None:
    import json
    await _ensure_table()
    pool = await get_pool()
    if pool is None:
        return
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO ticket_interactions
              (tenant_id, ticket_key, event_type, intent_hash, actor, actor_id,
               body, interpretation, decision, jira_comment_id, metadata)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11)
            """,
            tenant_id, ticket_key, event_type,
            kw.get("intent_hash"), kw.get("actor"), kw.get("actor_id"),
            (kw.get("body") or "")[:4000], kw.get("interpretation"),
            kw.get("decision"), kw.get("jira_comment_id"),
            json.dumps(kw.get("metadata") or {}),
        )


def record_event(ticket_key: str, event_type: str, **kw) -> None:
    """Fire-and-forget write of one interaction event. Safe from sync callers."""
    try:
        from .config_registry import get_tenant_id
        tenant = get_tenant_id()
    except Exception:
        import os
        tenant = os.environ.get("TENANT_ID", "isrds")
    fire_and_forget(_insert(tenant, ticket_key, event_type, **kw))


async def recall_interactions(ticket_key: str, limit: int = 20) -> list[dict]:
    """Full recent interaction timeline for a ticket (oldest→newest)."""
    try:
        from .config_registry import get_tenant_id
        tenant = get_tenant_id()
    except Exception:
        import os
        tenant = os.environ.get("TENANT_ID", "isrds")
    pool = await get_pool()
    if pool is None:
        return []
    await _ensure_table()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT event_type, intent_hash, actor, body, interpretation,
                   decision, created_at
            FROM ticket_interactions
            WHERE tenant_id=$1 AND ticket_key=$2
            ORDER BY created_at ASC
            LIMIT $3
            """,
            tenant, ticket_key, limit,
        )
    return [dict(r) for r in rows]


async def ask_already_answered(ticket_key: str, intent: str) -> bool:
    """True if Danielle already made an ask with this intent_hash AND a human
    reply (or her own close/escalate) followed it — i.e. re-asking is noise.
    """
    try:
        from .config_registry import get_tenant_id
        tenant = get_tenant_id()
    except Exception:
        import os
        tenant = os.environ.get("TENANT_ID", "isrds")
    pool = await get_pool()
    if pool is None:
        return False
    await _ensure_table()
    async with pool.acquire() as conn:
        ask = await conn.fetchrow(
            """
            SELECT created_at FROM ticket_interactions
            WHERE tenant_id=$1 AND ticket_key=$2 AND intent_hash=$3
              AND event_type='pmo_ask'
            ORDER BY created_at DESC LIMIT 1
            """,
            tenant, ticket_key, intent,
        )
        if not ask:
            return False
        resolved = await conn.fetchrow(
            """
            SELECT 1 FROM ticket_interactions
            WHERE tenant_id=$1 AND ticket_key=$2
              AND created_at > $3
              AND event_type IN ('human_reply','pmo_close_loop','pmo_escalate')
            LIMIT 1
            """,
            tenant, ticket_key, ask["created_at"],
        )
    return resolved is not None

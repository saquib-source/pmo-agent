-- 005_ticket_interactions.sql
-- Durable per-ticket interaction timeline for the PMO swarm.
-- Gives Danielle a human-like memory of what she asked, what humans replied,
-- how she interpreted it, and what she decided — so she stops re-nagging and
-- can route unresolved decisions to the right person.
--
-- Read by shared/decision.py (should_comment gate) and shared/conversation.py.
-- Companion to the existing pgvector agent_memory table (semantic recall) and
-- pending_actions (human-approval queue).

CREATE TABLE IF NOT EXISTS ticket_interactions (
  id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id     TEXT        NOT NULL,
  swarm_id      TEXT        NOT NULL DEFAULT 'pmo-swarm',
  ticket_key    TEXT        NOT NULL,

  -- What happened, in Danielle's terms:
  --   'pmo_ask'        — Danielle posted a question/request (a chase, a housekeeping ask)
  --   'human_reply'    — a human commented after a pmo_ask
  --   'pmo_interpret'  — Danielle's read of a human reply (no Jira write)
  --   'pmo_close_loop' — Danielle acknowledged / closed the thread
  --   'pmo_escalate'   — Danielle routed an open decision to the Accountable owner
  --   'pmo_silent'     — Danielle decided NO comment was needed (records the restraint)
  event_type    TEXT        NOT NULL,

  -- Stable hash of the *intent* of an ask (e.g. "fill epic+estimate+duedate on ISRDS-1498").
  -- Lets the decision gate answer "have I already asked this, and was it answered?"
  -- without fuzzy text matching. NULL for non-ask events.
  intent_hash   TEXT,

  actor         TEXT,                 -- display name of who acted (Danielle, or the human)
  actor_id      TEXT,                 -- Jira accountId when known
  body          TEXT,                 -- the comment / interpretation text
  interpretation TEXT,                -- Danielle's structured read (for human_reply events)
  decision      TEXT,                 -- chosen action: close_loop|escalate|new_followup|stay_silent
  jira_comment_id TEXT,               -- Jira comment id when this event posted to Jira
  metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ticket_interactions_ticket
  ON ticket_interactions (tenant_id, ticket_key, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_ticket_interactions_intent
  ON ticket_interactions (tenant_id, ticket_key, intent_hash);

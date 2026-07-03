-- Gross Opportunity Agent — Cloud SQL (PostgreSQL 15) DDL
-- The serving store the review screen reads and writes through the events API.
-- Apply once per Cloud SQL instance. Gap: instance name and connection string.

-- The live opportunity record the review list is served from.
CREATE TABLE opportunity (
  opportunity_id        TEXT PRIMARY KEY,
  project_identity_key  TEXT NOT NULL,
  project_name          TEXT,
  record_type           TEXT NOT NULL,
  stage                 TEXT,
  status                TEXT NOT NULL DEFAULT 'active',    -- active, closed, rejected
  street                TEXT,
  city                  TEXT,
  state                 TEXT,
  postal_code           TEXT,
  country               TEXT,
  owner                 TEXT,
  valuation             NUMERIC,
  bid_date              DATE,
  csi_divisions         JSONB DEFAULT '[]',
  gate_passed           BOOLEAN,
  gate_score            REAL,
  gate_matched_rules    JSONB DEFAULT '[]',
  primary_source_url    TEXT,
  closed_reason         TEXT,                               -- expired, withdrawn, awarded_elsewhere, null
  fetch_state           TEXT NOT NULL DEFAULT 'summary',    -- summary, pulling, full_pulled, failed
  fetch_error           JSONB,                              -- {code, message, failed_at} or null
  full_record           JSONB,                              -- present after a successful pull
  agent_trace           JSONB,                              -- per-record pipeline trace (normalize/dedup/gate steps)
  first_seen_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_changed_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON opportunity (status);
CREATE INDEX ON opportunity (gate_score DESC);
CREATE INDEX ON opportunity (project_identity_key);

-- Every source a record was seen in. Kept after dedup.
CREATE TABLE source_link (
  opportunity_id   TEXT NOT NULL REFERENCES opportunity(opportunity_id),
  source_name      TEXT NOT NULL,
  source_url       TEXT,
  source_record_id TEXT,
  PRIMARY KEY (opportunity_id, source_name, source_record_id)
);

-- Spec and addenda variants, filled on demand by pull_full_report.
CREATE TABLE spec_variant (
  opportunity_id TEXT NOT NULL REFERENCES opportunity(opportunity_id),
  source_name    TEXT,
  label          TEXT,
  url            TEXT,
  fetched_at     TIMESTAMPTZ,
  PRIMARY KEY (opportunity_id, source_name, label)
);

-- Per-user seen events. Source of truth for the new vs seen badge.
CREATE TABLE seen_event (
  opportunity_id TEXT NOT NULL REFERENCES opportunity(opportunity_id),
  user_id        TEXT NOT NULL,
  seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  via            TEXT NOT NULL,           -- scroll or opened
  PRIMARY KEY (opportunity_id, user_id)
);

-- Shared rejection state, one per opportunity.
CREATE TABLE rejection (
  opportunity_id TEXT PRIMARY KEY REFERENCES opportunity(opportunity_id),
  rejected       BOOLEAN NOT NULL DEFAULT true,
  rejected_by    TEXT,
  rejected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  reason_text    TEXT,
  rule_scope     TEXT,                    -- one_time or permanent
  rule_target    TEXT                     -- initial_screening or deep_criteria
);

-- The two editable rule lists, distinguished by list_name.
CREATE TABLE screening_rule (
  rule_id     TEXT PRIMARY KEY,
  list_name   TEXT NOT NULL,              -- initial_screening or deep_criteria
  description TEXT,
  kind        TEXT NOT NULL,              -- include or exclude
  field       TEXT NOT NULL,
  operator    TEXT NOT NULL,              -- matches, in, gte, lte, intersects, equals
  value       JSONB,
  source      TEXT NOT NULL DEFAULT 'seeded',   -- seeded or human
  owned_by    TEXT,                       -- pre_bidder on deep_criteria rows
  created_by  TEXT,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  active      BOOLEAN NOT NULL DEFAULT true
);

-- The scope, a single configuration row.
CREATE TABLE scope (
  id             INT PRIMARY KEY DEFAULT 1,
  csi_divisions  JSONB NOT NULL DEFAULT '[]',    -- [{code, label, status}]
  product_scope  JSONB NOT NULL DEFAULT '[]',
  project_types  JSONB NOT NULL DEFAULT '[]',
  geographies    JSONB NOT NULL DEFAULT '[]',
  hard_excludes  JSONB NOT NULL DEFAULT '[]',
  CONSTRAINT one_row CHECK (id = 1)
);

-- The per-source configuration and its verification state.
CREATE TABLE source_registry (
  source_id       TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  method          TEXT NOT NULL,          -- rest, oauth2, sftp, alert_email, inbound_push, firecrawl
  config          JSONB NOT NULL,         -- endpoints, field map ref, pagination
  watermark_field TEXT,                   -- cursor or last-modified field, null if none
  cadence_cron    TEXT,                   -- per-source schedule
  rate_limit      JSONB,
  cost            JSONB,                  -- verified or reported
  verified        TEXT NOT NULL DEFAULT 'unknown',   -- verified, reported, unknown
  enabled         BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Delta watermark per source.
CREATE TABLE watermark (
  source_id    TEXT PRIMARY KEY REFERENCES source_registry(source_id),
  last_cursor  TEXT,
  last_run_at  TIMESTAMPTZ
);

-- Agent activity feed — one row per pipeline step, for the live "what the agent is
-- doing" panel in the console. Written by the orchestrator/jobs; read by /api/activity.
CREATE TABLE IF NOT EXISTS agent_activity (
  id             BIGSERIAL PRIMARY KEY,
  ts             TIMESTAMPTZ NOT NULL DEFAULT now(),
  run_id         TEXT,
  source_id      TEXT,
  step           TEXT NOT NULL,                 -- pull, budget, fetch_full, normalize, dedup, gate, commit, watchdog, scout, error, run
  level          TEXT NOT NULL DEFAULT 'info',  -- info, good, drop, warn
  message        TEXT NOT NULL,
  opportunity_id TEXT,
  detail         JSONB,
  agent          TEXT                           -- swarm agent id (goa/agents_meta.py): orchestrator, connector, normalizer, dedup, gate, committer, watchdog, scout
);
CREATE INDEX IF NOT EXISTS agent_activity_ts_idx ON agent_activity (id DESC);
CREATE INDEX IF NOT EXISTS agent_activity_agent_idx ON agent_activity (agent, id DESC);
-- Migration for instances created before the agent column existed:
ALTER TABLE agent_activity ADD COLUMN IF NOT EXISTS agent TEXT;

-- Daily API request-budget ledger, one row per (source, UTC day). The orchestrator
-- reads it before a pull to size the run's request budget (never 429), and the
-- console shows used/remaining. SAM.gov quota resets 00:00 UTC → keyed on UTC date.
CREATE TABLE IF NOT EXISTS api_request_ledger (
  source_id     TEXT NOT NULL,
  day           DATE NOT NULL,
  requests_used INT  NOT NULL DEFAULT 0,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, day)
);

-- Idempotency markers. Atomic transaction partner of every opportunity write.
-- The unique PK guarantees concurrent writers fail rather than creating duplicates.
CREATE TABLE fired_marker (
  source_id          TEXT NOT NULL,
  source_record_hash TEXT NOT NULL,
  opportunity_id     TEXT,
  fired_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (source_id, source_record_hash)
);

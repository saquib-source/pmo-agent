-- =============================================================================
-- ISRDS Agentic Platform — Foundation Migration
-- Run against: isrds_agentic database on AlloyDB
-- =============================================================================

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "vector";

-- =============================================================================
-- Config Registry
-- One row per Role Category. Read at agent startup to wire engine/memory/tools.
-- =============================================================================
CREATE TABLE IF NOT EXISTS config_registry (
  role_category              TEXT PRIMARY KEY,
  swarm_template_id          TEXT,
  engine_binding             TEXT        NOT NULL,  -- e.g. claude-sonnet-4-6
  memory_surface             TEXT        NOT NULL,  -- pgvector | postgresql_ts | vertex_ai_rag
  tool_surface               TEXT        NOT NULL,  -- survey_tools | read_only_domain | etc.
  authority_gradient_version TEXT        NOT NULL DEFAULT 'v1',
  system_prompt              TEXT,
  updated_at                 TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Authority Gradient Versions
-- Immutable — never update rows, only INSERT new versions.
-- =============================================================================
CREATE TABLE IF NOT EXISTS authority_gradient_versions (
  id                    UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  role_category         TEXT        NOT NULL,
  version               TEXT        NOT NULL,
  decision_class        TEXT        NOT NULL CHECK (decision_class IN ('MUST_ESCALATE','DECIDE_AND_REPORT','DECIDE_SILENTLY')),
  escalation_triggers   JSONB       NOT NULL DEFAULT '[]',
  autonomy_ceiling      JSONB       NOT NULL DEFAULT '{}',
  created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by            TEXT        NOT NULL DEFAULT 'system',
  UNIQUE (role_category, version)
);

-- =============================================================================
-- Trust Ledger
-- Append-only. Trigger blocks UPDATE and DELETE.
-- =============================================================================
CREATE TABLE IF NOT EXISTS trust_ledger (
  id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id      TEXT        NOT NULL,
  swarm_id       TEXT,
  role_category  TEXT        NOT NULL,
  event_type     TEXT        NOT NULL,  -- AUDIT_FINDING | REPORTED_DECISION | SILENT_DECISION | ESCALATION_PENDING | ESCALATION_RESOLVED
  decision_class TEXT        NOT NULL,
  outcome        TEXT,                  -- POSITIVE | NEGATIVE | NEUTRAL
  evidence       JSONB       NOT NULL DEFAULT '{}',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Enforce append-only
CREATE OR REPLACE FUNCTION trust_ledger_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'trust_ledger is append-only: % operations are not permitted', TG_OP;
END;
$$;

DROP TRIGGER IF EXISTS trust_ledger_no_update ON trust_ledger;
CREATE TRIGGER trust_ledger_no_update
  BEFORE UPDATE OR DELETE ON trust_ledger
  FOR EACH ROW EXECUTE FUNCTION trust_ledger_immutable();

-- RLS
ALTER TABLE trust_ledger ENABLE ROW LEVEL SECURITY;
CREATE POLICY trust_ledger_tenant_isolation ON trust_ledger
  USING (tenant_id = current_setting('app.tenant_id', true));

-- =============================================================================
-- Escalation Taxonomy
-- =============================================================================
CREATE TABLE IF NOT EXISTS escalation_taxonomy (
  id               UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  swarm_id         TEXT,
  role_category    TEXT        NOT NULL,
  escalation_class TEXT        NOT NULL CHECK (escalation_class IN ('MUST_ESCALATE','DECIDE_AND_REPORT','DECIDE_SILENTLY')),
  context_pattern  JSONB       NOT NULL DEFAULT '{}',
  handler_type     TEXT        NOT NULL,  -- PUBSUB | SMS | WEBHOOK
  handler_ref      TEXT        NOT NULL,  -- topic name, phone number, URL
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- Inter-Agent Trace
-- =============================================================================
CREATE TABLE IF NOT EXISTS inter_agent_trace (
  id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  trace_id        TEXT        NOT NULL,
  parent_trace_id TEXT,
  source_role     TEXT        NOT NULL,
  target_role     TEXT        NOT NULL,
  message_type    TEXT        NOT NULL,
  payload         JSONB       NOT NULL DEFAULT '{}',
  latency_ms      INTEGER,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_inter_agent_trace_trace_id ON inter_agent_trace (trace_id);

-- =============================================================================
-- Tool Call Audit
-- =============================================================================
CREATE TABLE IF NOT EXISTS tool_call_audit (
  id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  trace_id      TEXT        NOT NULL,
  role_category TEXT        NOT NULL,
  tool_name     TEXT        NOT NULL,
  input_hash    TEXT,
  output_hash   TEXT,
  duration_ms   INTEGER,
  status        TEXT        NOT NULL CHECK (status IN ('SUCCESS','ERROR','TIMEOUT')),
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_tool_call_audit_trace_id ON tool_call_audit (trace_id);
CREATE INDEX IF NOT EXISTS idx_tool_call_audit_role     ON tool_call_audit (role_category, created_at DESC);

-- =============================================================================
-- Configured Swarm Instances
-- =============================================================================
CREATE TABLE IF NOT EXISTS configured_swarm_instances (
  id                        UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id                 TEXT        NOT NULL,
  swarm_template_id         TEXT        NOT NULL,
  active_role_categories    JSONB       NOT NULL DEFAULT '[]',
  supervisor_trainer_user_id TEXT,
  status                    TEXT        NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','PAUSED','RETIRED')),
  created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE configured_swarm_instances ENABLE ROW LEVEL SECURITY;
CREATE POLICY swarm_tenant_isolation ON configured_swarm_instances
  USING (tenant_id = current_setting('app.tenant_id', true));

-- =============================================================================
-- Escalation Queue
-- =============================================================================
CREATE TABLE IF NOT EXISTS escalation_queue (
  id             UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  swarm_id       TEXT,
  tenant_id      TEXT        NOT NULL,
  role_category  TEXT        NOT NULL,
  decision_class TEXT        NOT NULL,
  context        JSONB       NOT NULL DEFAULT '{}',
  status         TEXT        NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','ACTIONED','OVERRIDDEN')),
  actioned_by    TEXT,
  actioned_at    TIMESTAMPTZ,
  created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
ALTER TABLE escalation_queue ENABLE ROW LEVEL SECURITY;
CREATE POLICY escalation_tenant_isolation ON escalation_queue
  USING (tenant_id = current_setting('app.tenant_id', true));

-- =============================================================================
-- Agent Memory (pgvector)
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_memory (
  id            UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
  tenant_id     TEXT        NOT NULL,
  role_category TEXT        NOT NULL,
  session_id    TEXT,
  summary       TEXT        NOT NULL,
  embedding     vector(768) NOT NULL,
  metadata      JSONB       NOT NULL DEFAULT '{}',
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_agent_memory_embedding
  ON agent_memory USING hnsw (embedding vector_cosine_ops);
ALTER TABLE agent_memory ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_memory_tenant_isolation ON agent_memory
  USING (tenant_id = current_setting('app.tenant_id', true));

-- =============================================================================
-- ISRDS PMO Swarm — Config Registry Seed
-- Run against: isrds_agentic database on AlloyDB
-- Depends on: 001_foundation.sql
-- =============================================================================

-- ── Config Registry: PMO Orchestrator role ────────────────────────────────────
INSERT INTO config_registry (
  role_category,
  swarm_template_id,
  engine_binding,
  memory_surface,
  tool_surface,
  authority_gradient_version,
  system_prompt
)
VALUES (
  'PMO Orchestrator',
  'pmo-swarm-v1',
  'gemini-2.5-flash',
  'pgvector+session',
  'pmo_tools',
  'v1',
  'You are the PMO Orchestrator for ISRDS Systems. You drive the daily Operating Brief cycle across all Jira projects. You coordinate five skill agents: execution_tracking (find stalled work), follow_up (draft chase pings), ownership_raci (audit RACI gaps), feature_completeness (audit built vs unbuilt features), hygiene (check ticket quality). You enforce governance gates before any Jira write — no comment, transition, assignment, or escalation happens without a human approval gate. You synthesise all findings into a clear Operating Brief. You operate at Decide and Report authority for all read operations. You must open a governance gate for all write operations.'
)
ON CONFLICT (role_category) DO UPDATE SET
  engine_binding             = EXCLUDED.engine_binding,
  memory_surface             = EXCLUDED.memory_surface,
  tool_surface               = EXCLUDED.tool_surface,
  authority_gradient_version = EXCLUDED.authority_gradient_version,
  system_prompt              = EXCLUDED.system_prompt,
  updated_at                 = NOW();

-- ── Authority Gradient v1: PMO Orchestrator ───────────────────────────────────
INSERT INTO authority_gradient_versions (
  role_category,
  version,
  decision_class,
  escalation_triggers,
  autonomy_ceiling
)
VALUES (
  'PMO Orchestrator',
  'v1',
  'DECIDE_AND_REPORT',
  '["board_critical_stall", "feature_pct_built_below_20", "raci_gap_count_above_threshold", "hygiene_score_below_0_5"]',
  '{
    "actions_permitted": ["scan_jira", "read_raci", "audit_features", "check_hygiene", "draft_comment", "log_decision", "read_ledger"],
    "actions_requiring_gate": ["post_comment", "request_transition", "assign_ticket", "escalate"],
    "gate_types": {
      "post_comment":       "Review",
      "request_transition": "Approve",
      "assign_ticket":      "Approve",
      "escalate":           "Escalate"
    }
  }'
)
ON CONFLICT (role_category, version) DO NOTHING;

-- ── Configured Swarm Instance: ASHS PMO Swarm ────────────────────────────────
INSERT INTO configured_swarm_instances (
  tenant_id,
  swarm_template_id,
  active_role_categories,
  status
)
VALUES (
  'ashs',
  'pmo-swarm-v1',
  '["PMO Orchestrator", "execution_tracking_agent", "follow_up_agent", "ownership_raci_agent", "feature_completeness_agent", "hygiene_agent"]',
  'ACTIVE'
)
ON CONFLICT DO NOTHING;

-- ── daily_briefings: add swarm_id column if not present ──────────────────────
-- (001_foundation.sql defines daily_briefings without swarm_id in some versions)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_name = 'daily_briefings' AND column_name = 'swarm_id'
  ) THEN
    ALTER TABLE daily_briefings ADD COLUMN swarm_id TEXT;
  END IF;
END $$;

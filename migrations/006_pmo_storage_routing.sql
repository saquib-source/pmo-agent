-- =============================================================================
-- ISRDS PMO Swarm — Storage Routing Correction
-- Run against: isrds_agentic database on Cloud SQL Postgres
-- Depends on: 001_foundation.sql
--
-- Architectural decision:
--   PostgreSQL holds relational/operational data only:
--     config_registry, authority_gradient_versions, configured_swarm_instances
--     pending_actions, escalation_queue (ACID state machines)
--     agent_memory (pgvector — semantic search, cannot move to BQ)
--     ticket_interactions (per-ticket decision gate, needs sub-second reads)
--
--   BigQuery (isrds_pmo dataset) holds all agent-generated data:
--     operating_briefs, cycle_metrics, trust_events, stalled_tickets,
--     hygiene_findings, raci_gaps, feature_snapshot,
--     inter_agent_trace, tool_call_audit  ← moved here from Postgres
--
--   Tables NOT dropped here (owned by Survey Agent / platform):
--     trust_ledger     — survey_sessions.trust_ledger_id references it via FK;
--                        PMO write path removed in shared/governance.py.
--     daily_briefings  — owned by Survey Agent Synthesizer (002_survey.sql);
--                        PMO write path removed in pmo_daemon.py.
-- =============================================================================

-- Drop inter_agent_trace — no active write code; analytics belong in BigQuery.
DROP TABLE IF EXISTS inter_agent_trace;

-- Drop tool_call_audit — no active write code; analytics belong in BigQuery.
DROP TABLE IF EXISTS tool_call_audit;

-- Ensure escalation_taxonomy is also cleaned up if unused by any agent.
-- (Left in place — Survey Agent may use it via escalation_queue.)

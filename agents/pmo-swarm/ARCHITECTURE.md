# PMO-Swarm Architecture & Data Flow

## System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ISRDS PMO SWARM (pmo-swarm)                     │
│                      Multi-Agent Orchestrator Platform                  │
└─────────────────────────────────────────────────────────────────────────┘

                              ┌──────────────────┐
                              │  Cloud Scheduler │  (fires every SCAN_INTERVAL)
                              └────────┬─────────┘
                                       │
                    ┌──────────────────▼──────────────────┐
                    │     Cloud Run Job (pmo-swarm)      │
                    │     python pmo_daemon --once        │
                    └──────────────────┬──────────────────┘
                                       │
                 ┌─────────────────────▼─────────────────────┐
                 │  pmo_daemon.py → _startup() → _run_cycle()│
                 │  Creates ADK Runner + Session Service      │
                 └─────────────────────┬─────────────────────┘
                                       │
                ┌──────────────────────▼──────────────────────┐
                │        PMO ORCHESTRATOR (root_agent)        │
                │     Type: google.adk.agents.LlmAgent        │
                │     Model: Gemini 2.5 Flash (from Config)   │
                │     Authority: DECIDE_AND_REPORT + GATES    │
                └──────────────────────┬──────────────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
        │         ┌────────────────────▼──────────────────┐            │
        │         │      Agent Tool Router                │            │
        │         │ (fan out to skill agents + tools)     │            │
        │         └────────────────────┬──────────────────┘            │
        │                              │                              │
        ▼                              ▼                              ▼

    ┌─────────────┐           ┌─────────────┐            ┌─────────────┐
    │ Agent Tool  │           │ Agent Tool  │            │ Agent Tool  │
    │ (sub_agent) │           │ (sub_agent) │            │ (sub_agent) │
    │             │           │             │            │             │
    │ Execution   │           │  Follow-Up  │            │ Ownership   │
    │ Tracking    │           │  Agent      │            │ RACI Agent  │
    │ Agent       │           │ (escalate)  │            │ (escalate)  │
    │             │           │             │            │             │
    └─────┬───────┘           └─────┬───────┘            └─────┬───────┘
          │                         │                          │
    ┌─────▼──────────────────────────▼──────────────────────────▼───────┐
    │              ADK Swarm Execution Layer (Layer 2)                   │
    │  (Orchestrates parallel execution of agents + tool calls)          │
    └─────┬───────────────────────────────────────────────────────────┬─┘
          │                                                           │
          ▼                                                           ▼

┌─────────────────────────┐              ┌──────────────────────────┐
│   External Data Sources │              │   Swarm-Level Services   │
├─────────────────────────┤              ├──────────────────────────┤
│ • Jira Cloud            │              │ • Config Registry (L1)   │
│ • Firestore (nav catalog)              │ • Session Service (L4)   │
│ • AlloyDB (brief backup)               │ • Governance Gates (L5)  │
│ • BigQuery (analytics)  │              │ • Trust Ledger (L5)      │
└─────────────────────────┘              │ • Cloud Logging (L8)     │
                                         │ • Cloud Monitoring (L8)  │
                                         └──────────────────────────┘
```

---

## Data Flow: Cycle Execution

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         ONE PMO SWARM CYCLE                             │
└─────────────────────────────────────────────────────────────────────────┘

START: pmo_daemon.py --once  (or Cloud Scheduler triggers job)

STEP 1: _startup()
    └─ Load config from AlloyDB config_registry table
    └─ Open database connection pools (AlloyDB, Firestore, BigQuery)
    └─ Initialize ADK Session Service (Layer 4: VertexAiSessionService)
    └─ Create ADK Runner with root_agent
    └─ Log "Startup complete" to Cloud Logging

STEP 2: _build_brief_prompt(mode)
    └─ mode ∈ { "full", "brief_only" }
    └─ Construct orchestrator prompt with:
        • Current timestamp
        • JIRA_PROJECTS list
        • AUTO_COMMENT flag (are we drafting chase messages?)
    └─ Return full prompt string

STEP 3: _run_prompt(prompt, session_id)
    └─ Check if ADK session exists (get_session by session_id)
    └─ If not, create_session with user_id="pmo_daemon"
    └─ Send message to _runner.run_async()
    └─ Stream responses as events:
        • event.is_final_response() → collect text parts
        • Append to full_response[]
    └─ Return joined response string

STEP 4: Orchestrator processes prompt
    ├─ Routes to 5 skill agents (ADK AgentTools):
    │  1. execution_tracking_agent    → query Jira JQL → return stalled tickets
    │  2. follow_up_agent             → draft messages (authority: MUST_ESCALATE)
    │  3. ownership_raci_agent        → audit gaps    (authority: MUST_ESCALATE)
    │  4. feature_completeness_agent  → scan Firestore → feature snapshot
    │  5. hygiene_agent               → scan Jira → violations
    │
    ├─ Agents call shared tools (Layer 3: tool_registry):
    │  • jira_search_jql, jira_get_issue
    │  • governance_gate (Review, Approve, Escalate)
    │  • firestore_query, alloydb_query
    │  • log_decision, read_ledger
    │
    ├─ Trust Ledger (Layer 5) captures all gate opens:
    │  • {"type": "gate", "gate_type": "Review", "detail": "...", ...}
    │  → Written to adk/trust-ledger.jsonl (local backup)
    │  → Also logged to Cloud Logging (isrds-pmo-swarm)
    │
    └─ Orchestrator synthesizes findings into Operating Brief text

STEP 5: _save_brief_to_db()
    ├─ Write to AlloyDB daily_briefings table
    ├─ Write to local file: briefs/brief_YYYYMMDD_HHMMSS.txt
    └─ Symlink briefs/latest.txt → today's brief

STEP 6: log_operating_brief() → BigQuery isrds_pmo.operating_briefs
    └─ INSERT: cycle_ts, mode, brief_text, duration_ms, session_id

STEP 7: log_cycle_metrics() → BigQuery isrds_pmo.cycle_metrics
    └─ INSERT: cycle_ts, mode, projects, duration_ms, agent results

STEP 8: Observability (Layer 8)
    ├─ emit trace_agent_run() span to Cloud Logging
    ├─ record_metric("briefs_generated_total") → Cloud Monitoring
    ├─ record_metric("agent_run_duration_ms") → Cloud Monitoring
    └─ All governance_gate opens logged to Cloud Logging (severity: WARNING)

STEP 9: Cleanup
    └─ Print response to stdout (captured by Cloud Logging)
    └─ Return response string to pmo_daemon

END: Container exit(0) — Cloud Run job completes

NEXT: Cloud Scheduler waits SCAN_INTERVAL minutes, fires next job
```

---

## Data Model: 8 Architectural Layers

| Layer | Name | Implementation | Purpose | Artifact |
|-------|------|----------------|---------|----------|
| **1** | Config Registry | `shared/config_registry.py` | Runtime config (model, projects, scan interval) | `agent_spec.yaml` |
| **2** | Agent Runtime | `google.adk.runners.Runner` | ADK orchestrator for swarm execution | N/A (framework) |
| **3** | Tools | `shared/tool_registry.py` (22 tools) | All tools agents can invoke (Jira, Firestore, governance) | `tool_registry.yaml` |
| **4** | Memory/Sessions | `shared/memory.py` | ADK VertexAiSessionService for agent state | `memory_schema.yaml` |
| **5** | Governance | `shared/governance.py` + `governance-rules.yaml` | Approval gates + Trust Ledger | `governance-rules.yaml` |
| **6** | Human Override | `orchestrator.governance_gate()` | Review/Approve/Escalate gates before Jira writes | N/A |
| **7** | Workflow | `pmo_daemon.py` + scheduled loop | Periodic orchestrator invocation | `workflow.yaml` |
| **8** | Observability | `shared/observability.py` | Cloud Logging + Monitoring + traces | N/A |

---

## Authority Gradient

```
┌──────────────────────────────────────────────────────────────────┐
│ Agent Authority Levels (defined in agent_spec.yaml)              │
└──────────────────────────────────────────────────────────────────┘

DECIDE_AND_REPORT (read-only agents)
├─ execution_tracking_agent
│  └─ Scans Jira, reports stalled tickets
│  └─ No approval needed (read-only)
│  └─ Log to Trust Ledger + Cloud Logging
│
├─ feature_completeness_agent
│  └─ Reads Firestore feature catalog
│  └─ No approval needed
│
└─ hygiene_agent
   └─ Scans Jira for field violations
   └─ No approval needed

MUST_ESCALATE (write agents)
├─ follow_up_agent
│  ├─ Can DRAFT chase messages
│  ├─ CANNOT post to Jira until Review gate passes
│  └─ governance_gate("Review", ...) ← waits for human approval
│
└─ ownership_raci_agent
   ├─ Can IDENTIFY gaps
   ├─ CANNOT assign tickets until Approve gate passes
   └─ governance_gate("Approve", ...) ← waits for human approval

ESCALATE (critical findings)
└─ Any agent can call:
   └─ governance_gate("Escalate", ...) for critical path blockers
      └─ e.g., "ISRDS-1151 critical path stalled 169h"
```

---

## Governance Gates: How They Work

```
SCENARIO: follow_up_agent drafts a chase message

1. Agent drafts: "Hey @assignee, ISRDS-1151 stalled 72h, need status"

2. Agent calls: governance_gate("Review", description="...", ticket_key="ISRDS-1151")
   └─ Returns: {"status": "pending", "message": "⏸ Review gate — awaiting human decision"}

3. Trust Ledger entry created:
   {"type": "gate", "gate_type": "Review", "ticket_key": "ISRDS-1151", ...}

4. Cloud Logging entry emitted:
   severity: WARNING
   textPayload: "[pmo_orchestrator] governance_gate_opened: Review: [ISRDS-1151]"

5. HUMAN REVIEWS (via Cloud Logging UI, Slack notifier, or custom dashboard)
   └─ Decides: Approve, Deny, or Modify

6. CUSTOM GATE HANDLER (to be implemented):
   └─ Calls approval_service.update_gate(gate_id, decision="approve")
   └─ Agent resumes and posts comment to Jira
   └─ Log to Cloud Logging + Trust Ledger: "gate approved"

Currently: Gates surface in Cloud Logging, no auto-approval backend.
Next: Wire gates to Slack, PagerDuty, or custom approval dashboard.
```

---

## Data Storage: Four Destinations

```
┌─────────────────────────────────────────────────────────────────┐
│              WHERE PMO DATA LIVES                               │
└─────────────────────────────────────────────────────────────────┘

1️⃣  LOCAL FILES (always)
    Location: agents/pmo-swarm/adk/
    ├─ trust-ledger.jsonl
    │  └─ Every decision + gate + agent run
    │  └─ Append-only, human-readable JSON lines
    │  └─ Backup if Cloud Logging fails
    │
    └─ briefs/
       ├─ brief_YYYYMMDD_HHMMSS.txt
       │  └─ Full Operating Brief (plain text)
       │  └─ Kept indefinitely on Cloud Run persistent disk
       │
       └─ latest.txt
          └─ Symlink to today's latest brief

2️⃣  CLOUD LOGGING (real-time + searchable)
    Project: isr-division-systems-488723
    Log Name: projects/.../logs/isrds-pmo-swarm
    ├─ agent_run_start
    ├─ agent_run_complete (with duration_ms)
    ├─ governance_gate_opened (severity: WARNING)
    ├─ decision_logged
    └─ All structured fields searchable (trace_id, gate_type, ticket_key, ...)
    
    Query: gcloud logging read "logName=projects/.../logs/isrds-pmo-swarm" --stream

3️⃣  BIGQUERY (primary: analytics + briefs as system of record)
    Dataset: isr-division-systems-488723.isrds_pmo
    Tables:
    ├─ operating_briefs (columns: cycle_ts, mode, brief_text, duration_ms, session_id)
    │  └─ One row per cycle
    │  └─ brief_text is the full synthesized Operating Brief
    │  └─ System of record for briefs (not AlloyDB)
    │
    ├─ cycle_metrics (columns: cycle_ts, mode, projects, duration_ms, ...)
    │  └─ One row per cycle
    │  └─ Derived metrics: stalled_count, hygiene_score, raci_gaps, etc.
    │
    └─ governance_gates (columns: cycle_ts, gate_type, description, ticket_key, ...)
       └─ One row per gate opened
       └─ Track approval patterns over time

    Query: SELECT * FROM isrds_pmo.operating_briefs ORDER BY cycle_ts DESC LIMIT 10;

4️⃣  ALLOYDB (backup: fast operational reads)
    Instance: pmo-brief-db (us-central1-a)
    Database: pmo_agent
    Tables:
    ├─ daily_briefings (columns: tenant_id, briefing_date, summary_text, ...)
    │  └─ One row per date
    │  └─ Upsert on conflict (latest brief wins)
    │  └─ Kept for 30 days (retention policy)
    │
    └─ config_registry (columns: tenant_id, key, value, updated_at)
       └─ Central source of truth for agent config
       └─ Pulled at startup by pmo_daemon._startup()

    Query: SELECT * FROM daily_briefings ORDER BY briefing_date DESC LIMIT 10;
```

---

## Monitoring & Observability Checklist

### Health Check (every cycle)

- [ ] Cloud Run job started (resource.type=cloud_run_job)
- [ ] Container exit code = 0 ("Container called exit(0)")
- [ ] Startup complete logged (contains "Startup complete — projects: ...")
- [ ] Agent run start logged (agent_run_start event)
- [ ] Agent run complete logged (agent_run_complete with duration_ms)
- [ ] Brief saved locally (briefs/brief_*.txt exists)
- [ ] Brief saved to BigQuery (operating_briefs table)
- [ ] Metrics recorded to Cloud Monitoring (briefs_generated_total counter)

### Alerts to Set Up

```yaml
alerts:
  - name: PMO_Job_Failed
    condition: "Cloud Run job exit code != 0"
    severity: HIGH
    action: Notify #pmo-ops Slack channel

  - name: Governance_Gate_Escalation
    condition: "governance_gate_opened AND severity=WARNING"
    severity: HIGH (escalate gates only)
    action: Page on-call PMO lead

  - name: Stalled_Tickets_Critical
    condition: "execution_tracking_agent returns stall_count > 5"
    severity: MEDIUM
    action: Email PMO team lead

  - name: BigQuery_Write_Failed
    condition: "log_cycle_metrics exception"
    severity: LOW (AlloyDB backup is present)
    action: Alert SRE team
```

---

## Quick Reference: Common Queries

```bash
# Stream latest logs (realtime)
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=pmo-swarm" \
  --project=isr-division-systems-488723 \
  --limit=50 --stream

# Find governance gates opened
gcloud logging read "textPayload=~'governance_gate_opened'" \
  --project=isr-division-systems-488723 \
  --limit=20

# Query Operating Briefs
bq query --use_legacy_sql=false '
  SELECT cycle_ts, mode, LENGTH(brief_text) as brief_length, duration_ms
  FROM `isr-division-systems-488723.isrds_pmo.operating_briefs`
  ORDER BY cycle_ts DESC
  LIMIT 10
'

# Check daily_briefings in AlloyDB
gcloud sql connect pmo-brief-db --user=postgres -d pmo_agent << EOF
SELECT briefing_date, LENGTH(summary_text) as brief_len, generated_by_role
FROM daily_briefings
ORDER BY briefing_date DESC LIMIT 10;
EOF
```

---

**Last Updated**: 2026-06-24  
**Author**: Mohd Saquib  
**Architecture Version**: ISRDS Complete Architecture Document v2.2

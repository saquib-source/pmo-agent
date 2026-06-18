# Artifact 4: Memory Schema (Phase 2, parallel) + the Four-Destination Data Model

The memory schema declares what the agent persists **in Vertex AI Agent Engine** — and, just
as importantly, what it does *not*, because most data does not belong there.

## The four-destination model (Section 5.1)

Every data field has exactly one correct home. Placement is by access pattern, not build
convenience. The current app's "Firestore for everything" was build convenience; this corrects
it.

| Destination | What belongs there | Why |
|-------------|--------------------|-----|
| **Cloud SQL for PostgreSQL** | Workorders, customers, Service Providers, invoices, payments, scheduling, job history, survey responses — anything queried relationally or feeding EASS-IPV-ROI. | Structured transactional entities with joins. The 83 specs query this constantly. |
| **Firestore** | Real-time state needing sub-second client sync: active job status, live installer location, notification queues. | Its real-time listener architecture beats Postgres for this. **Never migrate it.** |
| **Vertex AI Agent Engine** | Agent memory, agent state between runs, cross-session agent context. **Nothing else.** | Purpose-built for agent memory with tenant isolation. Billed per use since Feb 2026 — scope discipline is cost discipline. |
| **BigQuery** | Analytics, reporting aggregates, historical data, EASS feeds, Leadership Dashboard. | Columnar analytics at scale. Fed from Postgres via Data Transfer Service. |

**The rule that catches most mistakes:** durable business records belong in **PostgreSQL**, not
in Agent Engine memory. Agent Engine holds only what the agent needs to remember *as an agent*
between runs (e.g. "last run I flagged milestone X; has it moved?"). A survey response, a
workorder, a customer record — those are durable records and go to Postgres, keyed to the
workorder/Service Provider, with aggregates synced to BigQuery.

## Memory schema shape

```json
{
  "apiVersion": "isrds.agent/v1",
  "kind": "MemorySchema",
  "agent_id": "pmo-agent",
  "store": "vertex_agent_engine",
  "tenant_isolation": true,
  "fields": [
    {
      "name": "last_run_at",
      "type": "timestamp",
      "purpose": "cross-run continuity",
      "destination_justification": "agent state between runs — correctly in Agent Engine"
    },
    {
      "name": "open_flags",
      "type": "array<flag>",
      "purpose": "milestones already flagged so the agent doesn't re-flag",
      "destination_justification": "agent context, not a business record"
    }
  ],
  "explicitly_not_in_memory": [
    {
      "data": "project_records",
      "correct_destination": "postgres",
      "reason": "durable business records are relational, queried by EASS"
    },
    {
      "data": "daily_digest_history",
      "correct_destination": "bigquery",
      "reason": "historical/analytics aggregate"
    }
  ]
}
```

The `explicitly_not_in_memory` block is not decoration. It forces the placement decision to be
written down and lets the quality gate confirm every field the agent touches has a home. The
schema stays portable JSON so a memory-platform exit is an ETL, not a rewrite.

## Vector data

If the agent does semantic retrieval (RAG over the knowledge spine, doctrine search, workorder
history search), vectors start in **pgvector on Cloud SQL** — next to the relational entities
they describe — not in a separate vector DB. Upgrade to AlloyDB ScaNN only when pgvector is the
*measured* bottleneck. Do not fragment vectors across systems preemptively (Section 5.5).

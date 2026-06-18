# Artifact 6: Workflow Definition (Phase 4) + Human Gate Config (Phase 5)

This is the artifact the runtime actually executes (Layer 7, Google ADK). It holds **triggers**,
**input/output contracts**, **step sequencing**, and the **gate block**. Scheduling lives here.

## How scheduling and triggering work

An agent does not run continuously. It runs when a **trigger** fires. There are two trigger
kinds, and an agent can have one, the other, or both.

### 1. Time-of-day schedule (cron)

The agent runs on a clock. Example: the **PMO Agent runs daily at 07:00**. Mechanically this is
a Cloud Scheduler cron entry that invokes the agent's Vertex AI Agent Engine endpoint. You
declare it in the workflow; the deploy step (Phase 8) provisions the scheduler.

Use a schedule when the job is **calendar-shaped** — a daily operating picture, a weekly
rollup. The cadence is a property of time, not of any single business event.

### 2. Event-driven trigger

The agent runs when a **signal** arrives. Example: the **Customer Satisfaction Survey Agent**
is event-driven on the Closing Events Delivery verified-closeout signal — it fires per job, and
is **never time-of-day scheduled** (satisfaction is measured per job, not per calendar).
Mechanically this is a Pub/Sub subscription (or an ADK event hook) on a domain signal.

Use an event trigger when the job is **event-shaped** — one job closed, one threshold crossed,
one blocker aged past 24 hours. The cadence is a property of the business event, not the clock.

### Both at once

An agent can be scheduled *and* event-driven. The PMO Agent runs daily at 07:00 **plus**
event-driven triggers: milestone slip, budget threshold crossed, blocker open over 24 hours. A
scheduled base cadence with event-driven interrupts is a common and correct pattern.

### The selection rule

Ask "is the work driven by time or by an event?" Calendar-shaped → schedule. Event-shaped →
event trigger. If real-world events drive it but you also want a guaranteed floor cadence, use
both. Do not put a survey on a 9am cron because cron is familiar — that produces stale,
per-calendar measurement of a per-job reality.

## Workflow shape

```yaml
apiVersion: isrds.agent/v1
kind: WorkflowDefinition
agent_id: pmo-agent
triggers:
  schedule:
    - cron: "0 7 * * 1-5"        # daily 07:00, business days; provisioned via Cloud Scheduler
      timezone: "America/Chicago"
  events:
    - signal: milestone_slip
      source: pubsub             # Pub/Sub topic or ADK event hook
    - signal: budget_threshold_exceeded
      source: pubsub
    - signal: blocker_aged_24h
      source: pubsub
contracts:
  input:  { ref: "agent-spec.yaml#/spec/inputs" }
  output: { ref: "agent-spec.yaml#/spec/outputs" }
steps:
  - id: gather
    tool: workorder_db
    action: query_workorders
  - id: assess
    note: "apply judgment rules from prompt.md"
  - id: distribute
    tool: jira
    action: comment_issue
    gate: Review                 # cleared before send (Phase 5 / gate-types.md)

# ---- Phase 5: Human Gate Config (written into this same file) ----
gates:
  - type: Review
    supervisor: COO
    sla: 30m
  - type: Escalate
    supervisor: Founder
    sla: 2h
  - type: Approve
    supervisor: Founder
    sla: none                    # Approve NEVER has a timeout; it waits indefinitely
    applies_to: ["all external communications"]
```

## Phase 5 notes

- Every gate gets a **supervisor** and an **SLA**, with one exception: **Approve has no SLA and
  no timeout fallback** — it is unconditional and waits indefinitely. The quality gate enforces
  this asymmetry (it fails if Approve carries a timeout, and fails if any non-Approve gate lacks
  a supervisor/SLA).
- Gate types and behaviors are in `references/gate-types.md`. Phase 4 wires *where* gates sit in
  the step sequence; Phase 5 fills in *who* and *how long*.

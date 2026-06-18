# Artifact 5: Governance Rules (Phase 3) — Layer 5

Governance is attached **after** the spec, prompt, tools, and memory exist, because the rules
engine reads the *full* agent to bound its action surface. Layer 5 is evaluated on **every
proposed agent action before ADK dispatches it**. With Layer 6 (human gates), it is the most
competitively differentiated asset in the platform and is fully portable ISRDS IP — it runs on
any infrastructure and needs no rebuild under any vendor exit.

## What governance rules do

For each action the agent can take (from the tool registry), a rule decides one of:

- **allow** — dispatch it.
- **gate** — route to a human gate (Review / Escalate / Approve) before dispatch.
- **deny** — never allowed for this agent regardless of context.

## Governance rules shape

```yaml
apiVersion: isrds.agent/v1
kind: GovernanceRules
agent_id: pmo-agent
invariants:
  # Platform-level rules every agent inherits. The quality gate enforces these.
  - id: no-vendor-in-runtime
    rule: "No action may select or hardcode a runtime engine; engine is config-resolved."
    on_violation: deny
  - id: irreversible-requires-approve
    rule: "Any action with irreversible:true in the tool registry requires an Approve gate."
    on_violation: gate
    gate: Approve
rules:
  - action: jira.comment_issue
    decision: gate
    gate: Review                 # output for distribution → human clears before send
    rationale: "External-facing artifact."
  - action: workorder_db.query_workorders
    decision: allow
    rationale: "Read-only internal query, no blast radius."
  - action: legacy_widget.push_update
    decision: gate
    gate: Approve                # irreversible → unconditional human approval
    rationale: "Irreversible external write."
escalation:
  - condition: "budget_threshold_exceeded"
    gate: Escalate
    to: Founder
    rationale: "Exceeds agent authority."
```

## The two invariants you never omit

1. **no-vendor-in-runtime** — backstops the platform's core rule at the action layer. Even if a
   vendor name leaked into a prompt, governance refuses to act on a hardcoded engine.
2. **irreversible-requires-approve** — every `irreversible: true` action in the tool registry
   maps to an Approve gate. This is the bridge between Layer 3 (what the agent *can* do) and
   Layer 6 (what a human must authorize). The quality gate cross-checks it.

## Writing good rules

- Default deny for anything outside the agent's stated purpose. A narrow purpose (Phase 1)
  makes this easy; a sprawling purpose makes governance impossible to bound.
- Rationale is required on every rule. A rule without a stated reason cannot be reviewed in
  Phase 7 and will be sent back.
- Keep rules in this portable YAML. They are trade-secret IP precisely because they are
  declarative and infrastructure-independent — that is what lets Layers 5 and 6 travel
  unchanged under every vendor-exit scenario.

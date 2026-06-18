# The Six Human Gate Types (Phase 5) — Layer 6

Agents do the work; humans make the decisions. Six gate types, configured per agent in Phase 5
and written into `workflow-definition.yaml`. (Section 8.)

| Gate | Trigger | Behavior |
|------|---------|----------|
| **Review** | Output ready for distribution | Agent continues other work; does not send until a human clears it. |
| **Escalate** | Condition exceeds agent authority | Full context package to a supervisor; agent continues. |
| **Approve** | Irreversible action proposed | **Hard stop. Waits indefinitely. No timeout, no auto-proceed. Unconditional.** |
| **Override** | Human disagrees with the agent's assessment | Human corrects; agent logs the disagreement with context and flags it for prompt improvement. |
| **Flag** | Agent notices something noteworthy | Low-priority, non-blocking notification; the human may dismiss. |
| **Kill** | Any time, any reason | Immediate halt with a clean checkpoint to memory. No auto-restart; resuming requires an explicit manual trigger. |

## How to assign gates

1. **Start from irreversibility.** Every action marked `irreversible: true` in the tool
   registry gets an **Approve** gate. No exceptions, no timeout. This is the rule the whole
   control architecture is built around.
2. **External-facing outputs get Review.** Anything that leaves the building (a JIRA comment, a
   partner-facing survey, an email) waits for a human to clear it before send.
3. **Authority boundaries get Escalate.** When the agent hits a condition beyond its remit
   (budget threshold, scope change), it packages context and escalates — and keeps working on
   everything else.
4. **Assessment disagreements get Override.** Used when a human looks at the agent's judgment
   and corrects it; the logged disagreement is a prompt-improvement signal, not just an
   override.
5. **Noteworthy-but-not-blocking gets Flag.** E.g. the Survey Agent flags detractor responses to
   Order Management Exception Handling — non-blocking, routed, dismissible.
6. **Kill is always available.** Every agent can be killed at any time with a clean checkpoint.
   You don't "assign" Kill so much as guarantee it.

## Worked examples from the two proving agents

- **PMO Agent:** Review (COO, 30m SLA), Escalate (Founder, 2h SLA), Approve (all external
  communications, unconditional). Three gates.
- **Customer Satisfaction Survey Agent:** Review on any survey-content change (partner-facing
  artifact), Flag on detractor responses (routes to Division 8 Dept 5). **No Approve gates** —
  sending a survey is not irreversible — which is exactly why it is one of the lowest-risk
  agents in the portfolio and an ideal second proving build.

## SLA rule

Every gate carries a supervisor and an SLA **except Approve**, which has neither a timeout nor a
fallback. An Approve gate that "auto-proceeds after N hours" is a defect — it converts an
irreversible action into an unsupervised one. The quality gate fails any Approve gate with a
timeout.

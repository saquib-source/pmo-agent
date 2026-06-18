# Authority Gradient + Trust Ledger (Phase 1 spec field, Phase 3 invariant)

Two platform primitives every ABSORB agent inherits from `BaseRoleAgent`, and every NEW agent
should still declare. The Authority Gradient bounds *what an agent may decide on its own*; the
Trust Ledger records *every decision and tool call it makes*. Together they are how an agent can
be autonomous and auditable at the same time.

## The Authority Gradient (spec field `spec.authority`)

An agent's authority level is a single enum that the governance layer reads to decide whether a
proposed action runs, gates, or is denied. It is **not** a vendor or model setting — it is an
ISRDS control-plane value, so it lives in the portable spec.

| Level | The agent may… | The agent may NOT… |
|-------|----------------|--------------------|
| `OBSERVE_ONLY` | read sources and persist agent memory | take any action that changes external state |
| `DECIDE_AND_REPORT` | read, reason, decide, and **report** its findings/recommendations; draft external actions | execute any external write or send without a human gate |
| `ACT_WITH_REVIEW` | execute reversible external actions after a Review clears them | execute irreversible actions without Approve |
| `ACT_AUTONOMOUS` | execute reversible actions without per-action review | execute irreversible actions without Approve (this invariant never relaxes) |

The PMO Agent operates at **`DECIDE_AND_REPORT`** (FR-8): it reads Firestore + Jira, decides
what is at-risk / orphaned / out of hygiene, and reports — but it **never writes to Jira or
communicates externally without the human gate.** When it needs to "chase like Danielle"
(comment on a ticket, set a field, @mention an owner), it *drafts* the action and a human
approves; it never auto-posts. That draft-then-gate pattern is `DECIDE_AND_REPORT` in practice.

```yaml
spec:
  authority: DECIDE_AND_REPORT          # OBSERVE_ONLY | DECIDE_AND_REPORT | ACT_WITH_REVIEW | ACT_AUTONOMOUS
```

### How authority interacts with gates

Authority sets the *ceiling*; gates and `irreversible:true` set the *floor*. A
`DECIDE_AND_REPORT` agent whose tool registry contains a Jira `comment_issue`/`set_field`
action marked `irreversible: true` (an external write) still needs an **Approve** gate for that
action — authority does not exempt it. The two systems compose; neither overrides the other.

## The Trust Ledger (governance invariant + audit)

Every agent **writes every decision to the Trust Ledger and audits every tool call**
(FR-7, consistent with `BaseRoleAgent`). This is an append-only record of "the agent proposed
X, governance decided Y, a human did Z" — it is what makes the autonomy reviewable after the
fact and is a primary input to the 5–7 day observation window (see `deploy.md`).

Add it as an **invariant** in `governance-rules.yaml` so the quality gate can confirm it is
present:

```yaml
invariants:
  - id: trust-ledger-audit
    rule: "Every decision and every tool call is written append-only to the Trust Ledger."
    on_violation: deny
```

For ABSORB agents the ledger already exists in the codebase — the invariant points at it, it
does not define a new store (see `07-build-posture.md`, the reuse rule). For NEW agents the
ledger is provisioned at deploy. Either way the artifact text is identical and portable.

## Why these are in the portable layer

Authority level and the Trust-Ledger invariant are declarative control-plane facts — they hold
no matter which engine the Config Registry resolves to. Like governance rules and gates, they
travel unchanged under any vendor exit. Keep them in the YAML; never push them into engine- or
infrastructure-specific code.

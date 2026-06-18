# Swarm requirements captured during the PMO hand-build

Running log of friction points. This is a primary deliverable of the proving build — it is
what tells the swarm where skill-agent boundaries add value vs coordination tax (Section 7.3).

## Friction points

1. **Spec → tool-registry id matching was manual.** The Spec Writer and Tool Registry Writer
   should share a single source of tool ids to avoid drift. The quality gate catches the
   mismatch after the fact, but the swarm should prevent it at generation time.

2. **Memory placement decisions took the most judgment.** Every field required a
   destination_justification. Worth a dedicated prompt section or decision tree in the swarm
   spec-writer, not a separate skill-agent, on current evidence.

3. **Authority level selection required understanding RACI context.** The DECIDE_AND_REPORT
   level was clear from the brief ("never auto-posts"), but a swarm agent would need to map
   natural-language authority descriptions to the four-level enum. A decision matrix from
   brief keywords → authority level would help.

4. **ABSORB posture wiring was the most integration-heavy phase.** Identifying BaseRoleAgent
   extension, Config Registry seed migration naming, and reuse declarations required knowledge
   of the existing codebase. The swarm must either have codebase context or ask. A pre-scan of
   the target codebase (`build.codebase`) for available primitives would eliminate guesswork.

5. **Dual-feed input declaration (Firestore + Jira) was ambiguous.** The brief mentioned both
   but the architecture says Firestore data flows through PostgreSQL (via debezium / CDC).
   The swarm must normalize input sources: if data originates in Firestore but is queried via
   PostgreSQL, the source should be `postgres`, not `firestore`.

6. **Trust Ledger invariant was easy to forget.** It's a governance-rules.yaml addition, not
   a separate file. The quality gate now catches it, but the swarm governance writer should
   include it by default rather than relying on the gate as a safety net.

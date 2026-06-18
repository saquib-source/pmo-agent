# Build Sequencing: Prove Before Swarm

Read this before building anything. It governs *whether* you should be hand-building an agent at
all, and *which* agent.

## The sequence (Section 7.4)

1. Hand-build the **PMO Agent** against the full eight-layer runtime.
2. Hand-build the **Customer Satisfaction Survey Agent** second.
3. Capture **every friction point** as a swarm requirement (the `swarm-requirements.md` file in
   each agent folder).
4. *Then* build the swarm incrementally — starting with the **Spec Writer** — adding skill
   agents only where the manual builds demonstrated real decomposition value.

## The hard cap

**Two to three hand-built agents before swarm industrialization. No more.** The swarm exists
because 83 specs across 4+ tenants do not scale artisanally. But industrializing artifact
production *before* the artifact formats are proven against the live runtime means rebuilding
the most expensive asset in the Build Architecture. So: prove the formats by hand twice, then
automate a validated process.

If a requirement arrives for a third+ agent and the swarm isn't stood up yet, **stop and
confirm** with the user. Either it's genuinely the third proving build, or the swarm work should
start. Don't quietly hand-build agent four.

## Why these two agents (the selection criterion)

Proving-agent selection is **maximum runtime-layer coverage at minimum blast radius** — *not*
tier label.

- **PMO Agent:** exercises all eight runtime layers, near-zero external risk (internal-only,
  tracks ISRDS projects). Also serves as the instrumentation vehicle for swarm-sizing
  measurement and the first real data source for the post-Feb-2026 Agent Engine cost model.
- **Survey Agent:** Tier 1 by EASS exception, Review-gated only, no irreversible actions. Lowest
  blast radius in the portfolio. It's Tier 2 by department but promoted to Tier 1 *timing*
  because the Satisfaction baseline must begin on day one of Order Management delivery — a
  baseline that starts in month three is an apology, not a sales asset.

A typical Tier 2 agent would prove *fewer* layers for comparable effort. That's why tier is not
the selector.

## Format parity is non-negotiable

Every hand-built agent must emit the **same six artifact types in the same formats** the swarm
will later generate. That is what makes the hand-built agents swarm-compatible rather than
legacy debt. Use the `templates/` shapes verbatim; don't invent a one-off structure because
you're "just hand-building this one."

## Swarm sizing: instrument before consolidating (Section 7.3)

The 10-orchestrator / 47-skill-agent design was rational for an older model generation. Current
models hold more context and may let several skill agents consolidate into fewer, broader ones —
reducing orchestration overhead and handoff failures. **Do not rearchitect the swarm
preemptively.** Instrument the PMO build to measure where skill-agent boundaries add value
versus coordination tax, and let the measured data drive consolidation. The directive is
explicit: consolidation follows data, not the model-capability headline.

## Build time vs calendar time (Section 7.2)

- **Build time** is agent-hours: 2–4 agent-hours to generate the six artifacts once the swarm
  exists; longer by hand.
- **Calendar time** is dominated by the **observation period**: 5–7 business days minimum of the
  agent running against real operational data before any tenant-facing release. This is a
  function of real-world event frequency, not model capability — it does **not** compress as
  engines improve.
- **Total first-build calendar time: 8–10 business days.** Never quote build-hours as delivery
  time.

---
name: isrds-agent-builder
description: >
  Build a production ISRDS agentic-platform agent inside Claude Code. Use this skill
  whenever someone gives a requirement for a new agent ("build an agent that...",
  "we need an agent for order management / surveys / PMO tracking", "spec out the X
  agent", "add a tenant agent"), or asks to generate any of the six portable artifacts
  (agent spec, prompt, tool registry, memory schema, governance rules, workflow
  definition). Trigger this even when the request only mentions one artifact or a tool
  like JIRA, a schedule, or a human-approval gate — those are all parts of one agent
  build. This skill enforces the 8-phase pipeline, the four-destination data model, the
  Config Registry vendor-neutrality rule, the six human gates, and the prove-before-swarm
  build sequencing. It outputs ADK-targeted, IDE-independent artifacts that deploy to
  Vertex AI Agent Engine + GCP and are identical whether hand-built or swarm-built.
license: Proprietary — ISRDS internal. Architecture authority: Complete Architecture Document v2.2.
---

# ISRDS Agent Builder

This skill manufactures one agent as **six portable artifacts** and walks it through the
**8-phase build pipeline** defined in the ISRDS Complete Architecture Document v2.2. The
artifacts are framework-targeted (Google ADK) and IDE-independent: they are produced the
same way whether a human hand-builds the agent now or the swarm generates it later. That
parity is the whole point — hand-built agents must be swarm-compatible, not legacy debt.

## The one rule you must not break

**No agent spec ever names a vendor or model.** Every agent resolves its runtime engine at
execution time through a single call: `config_registry.resolve(tenant_id, agent_id)`. ISRDS
may build every agent with Claude and still deliver a tenant whose agents run on Gemini,
Grok, or GPT. If you ever write `model: claude-...` or `model: gemini-...` into a spec,
prompt, or workflow, you have broken the platform's core IP. The build-time engine (what
writes the artifacts) and the runtime engine (what the deployed agent calls) are separate
layers. See `references/05-governance-rules.md` for how the quality gate enforces this.

## Before you build: which agent is this, and are we allowed to build it yet?

Read `references/build-sequencing.md` first. The short version:

- Only **two to three agents** are hand-built before the swarm is industrialized. As of v2.2
  those are the **PMO Agent** (first) and the **Customer Satisfaction Survey Agent** (second).
- If the requirement is for a *third+* agent and the swarm is not yet stood up, stop and
  confirm with the user. The doctrine caps manual builds; the swarm exists because 83 specs
  across 4+ tenants do not scale artisanally.
- Proving-agent selection criterion is **maximum runtime-layer coverage at minimum blast
  radius**, not tier label.

If the user is hand-building one of the two proving agents, proceed. Capture every friction
point as a swarm requirement (a running list in the build folder) — that capture is a
deliverable, not a side effect.

## The eight phases

Drive the build in this order. Phases 1–5 produce the six portable artifacts; 6 validates
them; 7 is human review; 8 generates deployable ADK code and ships.

| # | Phase | What you produce | Reference |
|---|-------|------------------|-----------|
| 1 | Spec Generation | `agent-spec.yaml` (vendor-neutral; build posture + authority) | `references/01-agent-spec.md`, `07-build-posture.md`, `08-authority-and-trust-ledger.md` |
| 2 | Parallel Elaboration | `prompt.md`, `tool-registry.yaml`, `memory-schema.json` | `references/02-prompt.md`, `03-tool-registry.md`, `04-memory-schema.md` |
| 3 | Governance Attachment | `governance-rules.yaml` (Layer 5) | `references/05-governance-rules.md` |
| 4 | Workflow Definition | `workflow-definition.yaml` (triggers, contracts, sequencing) | `references/06-workflow-definition.md` |
| 5 | Human Gate Config | gate block inside `workflow-definition.yaml` | `references/gate-types.md` |
| 6 | Quality Gate (automated) | pass/fail report | run `scripts/quality_gate.py` |
| 7 | Human Architecture Review | Manmeet approves — **never skipped** | — |
| 8 | ADK Code Gen + Deploy | `adk/` folder with deployable Python code | `references/deploy.md`, `references/10-mcp-server-setup.md` |

In Phase 2 the prompt, tool registry, and memory schema are independent and can be written
in any order (the swarm runs them concurrently; a human writes them back to back). Phases 3
and 4 are sequential because governance reads the *full* spec and the workflow wires
everything together.

### Build folder layout

Create one folder per agent and emit all six artifacts, the friction log, AND the
deployable ADK code:

```
agents/<agent_id>/
├── agent-spec.yaml          # Artifact 1
├── prompt.md                # Artifact 2
├── tool-registry.yaml       # Artifact 3
├── memory-schema.json       # Artifact 4
├── governance-rules.yaml    # Artifact 5
├── workflow-definition.yaml # Artifact 6 (+ gate config from Phase 5)
├── swarm-requirements.md    # friction log → feeds swarm build
└── adk/                     # Deployable Google ADK Python code (Phase 8)
    ├── __init__.py           # ADK entry point — exports root_agent
    ├── agent.py              # LlmAgent + MCPToolset connections
    ├── governance.py         # Layer 5 + Layer 6 runtime enforcement
    ├── requirements.txt      # Python dependencies
    └── .env.template         # environment variables (never commit secrets)
```

Copy the starter files from `templates/` into this folder and fill them in. The YAML/JSON/MD
templates are the canonical portable artifact shapes; the `templates/adk/` templates are the
canonical ADK code shapes. Both must be swarm-compatible.

## How to run a build (the loop)

1. **Clarify the requirement.** What does the agent do, for which tenant(s), what triggers
   it, what does it read/write, what actions are irreversible, who approves what. Also settle
   two things up front: the **build posture** — is this a NEW greenfield agent or an ABSORB
   into an existing codebase (extend `BaseRoleAgent`, seed the Config Registry by migration,
   reuse Trust Ledger / Authority Gradient / pgvector)? see `references/07-build-posture.md` —
   and the **authority level** the agent runs at (`OBSERVE_ONLY` … `ACT_AUTONOMOUS`), see
   `references/08-authority-and-trust-ledger.md`. If any of these is unknown, ask — don't
   guess. Irreversible actions decide the Approve gates, and getting them wrong is the one
   failure the architecture treats as unconditional.
2. **Phase 1 — spec.** Open `templates/agent-spec.yaml`, follow `references/01-agent-spec.md`.
   Record `authority:`, and for an ABSORB build fill the `build:` block (posture, codebase,
   `extends`, `role_category`, `config_registry_seed`, `reuse`).
3. **Phase 2 — elaborate.** Prompt, then tools, then memory. For tools, decide MCP-vs-custom
   per the rule in `references/03-tool-registry.md` (MCP is the default; custom only where no
   MCP server exists). For memory, place every data field against the four destinations in
   `references/04-memory-schema.md` — durable business records go to PostgreSQL, **not** Agent
   Engine memory.
4. **Phase 3 — governance.** Attach Layer 5 rules. The vendor-name check, the
   irreversible-action→Approve mapping, and the **trust-ledger-audit** invariant (every
   decision and tool call recorded; see `references/08-authority-and-trust-ledger.md`) live here.
5. **Phase 4 — workflow.** Triggers (schedule and/or event), input/output contracts, step
   sequencing. Scheduling lives here — see `references/06-workflow-definition.md`.
6. **Phase 5 — gates.** Assign the six gate types per `references/gate-types.md` and write the
   gate block into the workflow file with supervisors and SLAs.
7. **Phase 6 — quality gate.** Run `python scripts/quality_gate.py agents/<agent_id>/`. It
   checks: no vendor names anywhere, every tool referenced in the spec is registered, every
   gate has a supervisor and SLA (Approve excepted — it has no timeout), memory fields are all
   placed, authority is declared, trust-ledger invariant exists, and the six files exist.
   Fix every finding before Phase 7.
8. **Phase 7 — human review.** Present the artifact set to the architecture owner. Mandatory.
9. **Phase 8 — ADK code generation + deploy.** **Read `references/adk-gotchas.md` FIRST** —
   it contains every SSL, API, auth, and daemon error we hit in production. Then generate
   the `adk/` folder from the six artifacts. Copy `templates/adk/` into `agents/<agent_id>/adk/`, then:
   - In `agent.py`: replace the agent name, load `prompt.md`, and wire one `MCPToolset`
     per MCP tool in `tool-registry.yaml` (see `references/10-mcp-server-setup.md`).
     Custom tools (`integration: custom`) become `@FunctionTool` decorated Python functions.
   - In `governance.py`: it reads `governance-rules.yaml` at runtime — usually no edits.
   - In `.env.template`: fill in the MCP server URLs and tokens for each tool.
   - Test locally: `cd agents/<agent_id>/adk && adk web .` (opens http://localhost:8000).
   - If an MCP server doesn't exist for a tool, guide the user to build one
     (see `references/10-mcp-server-setup.md` Option B).
   - Deploy: `adk deploy --project $PROJECT --location us-central1`
   - Then **observe 5–7 business days minimum** against real operational data before any
     tenant-facing release. Build time is agent-hours; calendar time is dominated by
     observation. Do not conflate them.

## Cross-cutting references (read when relevant)

- `references/03-tool-registry.md` — **JIRA and any third-party tool**: MCP server first
  (registered via GCP Cloud API Registry), custom connector only as fallback. PostgreSQL
  access is itself an MCP server, not custom connector code.
- `references/04-memory-schema.md` — **the four-destination data model** and why durable
  records never live in Agent Engine memory.
- `references/06-workflow-definition.md` — **scheduling and triggers**: time-of-day vs
  event-driven, and how they map onto Cloud Scheduler / Pub-Sub.
- `references/gate-types.md` — the six human gates and how to assign them.
- `references/07-build-posture.md` — **NEW vs ABSORB**: extending `BaseRoleAgent`, seeding the
  Config Registry by migration, reusing platform primitives instead of rebuilding them.
- `references/08-authority-and-trust-ledger.md` — the **Authority Gradient** levels (e.g.
  `DECIDE_AND_REPORT`) and the **Trust Ledger** audit invariant.
- `references/build-sequencing.md` — prove-before-swarm, observation period, swarm sizing.
- `references/deploy.md` — Phase 8 ADK code generation, local testing (`adk web`), and
  Vertex AI Agent Engine deployment.
- `references/10-mcp-server-setup.md` — how to connect MCP servers (managed Rovo or custom
  Cloud Run) and map `tool-registry.yaml` entries to `MCPToolset` in `agent.py`.
- `references/adk-gotchas.md` — **READ THIS FIRST.** Every SSL, Jira API v3, ADK 2.x,
  daemon architecture, logging, and authentication error we hit in production, with fixes.
  Covers: corporate proxy certs, POST /search/jql pagination, ADF @mentions, standalone
  daemon vs ADK imports, Python logging buffering, and the full quick-start checklist.

Keep each artifact small and portable. The agent's value is in the governance rules (Layer 5)
and human gates (Layer 6) — those are ISRDS custom IP that travel unchanged under any vendor
exit. Everything else is configuration.

# ISRDS Agentic Platform

## Project Overview

**ISRDS** (Intelligent Systems Research & Development Services) is building a production-grade agentic platform that manufactures autonomous agents at scale using Google's Agent Development Kit (ADK) and Vertex AI Agent Engine on GCP.

The core mission is to enable any AI vendor's model (Claude, Gemini, GPT, Grok) to be plugged in at runtime through a vendor-neutral **Config Registry** — agents are built once, deployed anywhere.

## What You're Building

### Proving Agents (Hand-Built)
1. **PMO Agent ("Danielle")** — Project Management Office execution agent that monitors Jira, chases stalled tickets, detects blockers, generates daily Operating Briefs (primary proving agent)
2. **Survey Agent** — Customer Satisfaction Survey agent (secondary proving agent)

### The Platform (Coming Next)
- **Swarm Builder** — Orchestrated multi-agent system that manufactures 83+ agents across 4+ tenants
- **Config Registry** — Vendor-neutral runtime engine resolution (no model names hardcoded anywhere)
- **Trust Ledger** — Accountability log for every agent decision

## Key Constraints & Architecture Rules

### The Golden Rule
**No agent spec, prompt, or workflow ever names a vendor or model name.** Every agent resolves its runtime engine at execution time via:
```python
config_registry.resolve(tenant_id, agent_id)  # Returns model, tools, guardrails at runtime
```

This is the **core IP** of ISRDS. Breaking this makes agents legacy debt, not swarm-compatible.

### Build Pipeline
Agents go through **8 phases** with 6 human gates. See `.claude/skills/isrds-agent-builder/references/build-sequencing.md` for the sequencing doctrine.

### Six Portable Artifacts
Every agent is defined by these artifacts (IDE-independent, ADK-targeted):
1. **Agent Spec** (YAML) — System behavior, tools, memory, governance
2. **Prompt** (Markdown) — Behavioral instructions, reasoning style, constraints
3. **Tool Registry** (JSON) — All tools the agent can call with validation schemas
4. **Memory Schema** (JSON) — Persistent state the agent manages
5. **Governance Rules** (YAML) — Policy guardrails, escalation rules, approval gates
6. **Workflow Definition** (Python/ADK) — Multi-step orchestrations with backpressure

All six are vendor-agnostic and produced identically whether hand-built now or swarm-generated later.

## Directory Structure

```
isrds/
├── agents/                          # Proving agents (hand-built)
│   ├── pmo-swarm/                   # PMO Agent artifacts
│   │   ├── agent-spec.yaml
│   │   ├── prompt.md
│   │   ├── tool-registry.json
│   │   ├── memory-schema.json
│   │   ├── governance-rules.yaml
│   │   └── workflow.py
│   ├── survey_agents/               # Survey Agent artifacts
│   ├── gross-opportunity-agent/     # GOA — six-agent RFP-finding swarm (DEPLOYED, see below)
│   │   ├── goa/                     # orchestrator, adapters, normalize, dedup, gate, stores, events
│   │   ├── config/engines.json      # Config Registry: role → engine binding (committed)
│   │   ├── config/sources/*.json    # source configs (SAM.gov query plan, budget, field map)
│   │   ├── jobs/                    # seed, backfill, delta, expiration_sweep
│   │   ├── screen/index.html        # reviewer console (queue, agent swarm, activity, stats)
│   │   ├── sql/                     # Cloud SQL + BigQuery DDL
│   │   └── README.md                # full local-run + deploy runbook
│   └── bisd/                        # BISD: 34 agentic function scaffolds + org chart
│       └── org-chart/               # deployed interactive tree (generate_tree.py → tree.data.js)
├── migrations/                       # Database migrations & data transformations
├── .claude/
│   └── skills/
│       └── isrds-agent-builder/     # The skill that builds agents (6-artifact pipeline)
│           ├── SKILL.md             # Skill documentation
│           ├── references/          # Architecture reference docs
│           │   ├── 01-agent-spec.md
│           │   ├── 02-prompt.md
│           │   ├── 03-tool-registry.md
│           │   ├── 04-memory-schema.md
│           │   ├── 05-governance-rules.md
│           │   ├── 06-workflow-definition.md
│           │   ├── 07-build-posture.md
│           │   ├── build-sequencing.md
│           │   └── ...
│           ├── templates/           # Boilerplate for 6 artifacts
│           ├── examples/            # Reference implementations
│           └── scripts/
│               └── quality_gate.py  # Validator that enforces vendor-neutrality
├── README.md                        # User-facing setup & feature guide
└── CLAUDE.md                        # This file
```

## How to Use This Project

### To Build a New Agent
Invoke the skill:
```
/isrds-agent-builder
```

The skill will walk you through the 8-phase pipeline and generate all six artifacts. It validates that:
- No vendor/model names are hardcoded
- Memory schemas are coherent
- Tool registries have valid JSON schemas
- Governance rules are enforceable
- Workflows have correct backpressure

### To Understand an Artifact
Read the corresponding reference:
- **Agent Spec** → `references/01-agent-spec.md`
- **Prompt** → `references/02-prompt.md`
- **Tool Registry** → `references/03-tool-registry.md`
- **Memory Schema** → `references/04-memory-schema.md`
- **Governance Rules** → `references/05-governance-rules.md`
- **Workflow Definition** → `references/06-workflow-definition.md`

### To Deploy to GCP/ADK
See `references/deploy.md` — artifacts deploy as-is to Vertex AI Agent Engine.

## Gross Opportunity Agent (GOA) — the deployed production pattern

`agents/gross-opportunity-agent/` is the worked example of how we build agents here.
When creating or debugging agents, reuse these patterns (full runbook in its README.md):

- **Config Registry in practice** — function code calls `run_role("gate_classifier", …)`
  (`goa/engine/runner.py`); the role→engine binding lives ONLY in `config/engines.json`.
  Env overrides (still config, never code): `GOA_ENGINE_TRANSPORT=anthropic|vertex`,
  `GOA_ENGINE_OVERRIDE_VERSION` (+`_FAMILY`) for local runs.
- **Config-driven source adapters** — a new REST source is a JSON file in
  `config/sources/` (auth, pagination, field_map, query_plan) + registration via
  `jobs/seed.py`; adapter code stays generic (`goa/adapters/rest_adapter.py`).
- **API request budget (never 429)** — per-source `rate_limit.requests_per_day` +
  `reserve_for_ui`, persisted per UTC-day in Cloud SQL `api_request_ledger`; the
  orchestrator hands the adapter a per-run budget; adapter raises `BudgetExhausted`
  BEFORE exceeding it. Quota upgrade = env `GOA_REQUESTS_PER_DAY=1000` (wins) or edit
  config + re-run `jobs.seed`.
- **Swarm observability** — every pipeline step logs to `agent_activity` attributed to
  one of 8 agents (`goa/agents_meta.py`); the console's Agent Swarm tab renders
  per-agent status/logs from `/api/agents`, `/api/activity?agent=`, `/api/budget`.
- **Idempotency & dedup** — `fired_marker` hash (atomic, pre-model-cost), then
  `project_identity_key` merge, then fuzzy/embedding/model arbitration. Watermark only
  advances on fully-successful runs.

**Deployed (project isr-division-systems-488723, us-central1):**
| Resource | Value |
|---|---|
| Console (UI + Events API) | Cloud Run `goa-console` → https://goa-console-1059272334202.us-central1.run.app |
| Daily pull job | Cloud Run job `goa-daily-delta` (`python -m jobs.delta --source sam_gov`) |
| Schedule | Cloud Scheduler `goa-daily-delta-trigger`, `30 0 * * *` UTC (right after SAM.gov quota reset) |
| Secrets | `goa-cloudsql-dsn`, `sam-gov-api-key`, `goa-anthropic-api-key` (Secret Manager) |
| Serving store | Cloud SQL `tier3` / `isrds_db` (asyncpg, public IP) · lake: BigQuery dataset `goa` |
| BISD org chart | GCS `gs://isrds-bisd-org-chart` → https://storage.googleapis.com/isrds-bisd-org-chart/index.html (live-fetches GOA `/api/counts`; opens console in a full-screen modal) |

## Local dev environment quirks (Mohd's machine — corporate proxy)

- Google Python clients (gRPC: BigQuery/Firestore/SecretManager/Vertex) are proxy-blocked
  locally → set `GOA_SKIP_LAKE=1`, `GOA_SKIP_ACTIVITY=1`, use asyncpg + direct Anthropic
  API locally; verify gRPC paths on Cloud Run.
- HTTPS (httpx/requests) needs the combined CA bundle: `scripts/build_ca_bundle.sh`,
  then `SSL_CERT_FILE`/`REQUESTS_CA_BUNDLE` point at it. `GOA_FORCE_IPV4=1` (IPv6 to
  api.sam.gov is broken locally).
- **gcloud**: use `CLOUDSDK_PYTHON=/opt/homebrew/bin/python3.12` + one-time
  `gcloud config set core/custom_ca_certs_file <combined bundle>` — then `run deploy
  --source`, `scheduler`, `builds`, `storage` all work locally. (/usr/bin/python3 = 3.9
  crashes on newer gcloud surfaces.)
- `gcloud run deploy --source` needs the explicit `.gcloudignore` in the agent dir —
  without it gcloud mirrors .gitignore and drops `config/*.json` from the image.

## Key Files

| File | Purpose |
|------|---------|
| `.claude/skills/isrds-agent-builder/SKILL.md` | Skill documentation and entry point |
| `.claude/skills/isrds-agent-builder/scripts/quality_gate.py` | Enforces vendor-neutrality and artifact coherence |
| `agents/pmo-swarm/` | First proving agent (PMO Agent) |
| `agents/survey_agents/` | Second proving agent (Survey Agent) |
| `agents/gross-opportunity-agent/README.md` | GOA runbook: local run, deploy, budget, schedule |
| `agents/gross-opportunity-agent/config/engines.json` | Config Registry — the ONLY place engine bindings live |
| `agents/bisd/org-chart/generate_tree.py` | Org chart generator (PROTO block wires live GOA link/API) |
| `README.md` | User-facing setup, features, and troubleshooting |

## Important Rules

1. **Vendor-Neutral Config** — Never hardcode `model: claude-3-5-sonnet` or `model: gemini-2-pro`. Use `config_registry.resolve()` at runtime.
2. **Six Artifacts Always** — Don't skip any of the six portable artifacts, even for simple agents. Consistency enables swarm industrialization.
3. **Prove Before Swarm** — Only 2–3 agents are hand-built before the swarm automation kicks in. For agent 3+ requests, confirm sequencing with stakeholders.
4. **Artifact Coherence** — The quality gate validates that memory schemas, tool registries, and governance rules are cross-consistent (no tools used that aren't registered, no memory accessed without schema definition, etc.).

## Quick Reference: Skills & Commands

| Skill | When to Use |
|-------|------------|
| `/isrds-agent-builder` | Build a new agent (generates all 6 artifacts, runs 8-phase pipeline) |
| `/verify` | Test an agent in the ADK web UI to confirm behavior |
| `/code-review` | Review agent artifacts for architectural compliance |
| `/schedule` | Set up autonomous daemon runs for proving agents |

## Collaboration Notes

- **Author**: Mohd Saquib (saquib@isrdsystems.com)
- **Git user**: Mohd Saquib
- **Active branches**: Proving agent development on `main`
- **GCP Project**: `isr-division-systems-488723` (see `isr-division-systems-488723-d4303008d08a.json`)

## Next Steps

1. **PMO Agent**: Finish first proving agent (Jira integration, Gemini AI, daily Operating Briefs)
2. **Survey Agent**: Build second proving agent (customer feedback collection & analysis)
3. **GOA**: register SAM.gov system-account role (1,000 req/day, free, ~2–3 weeks) → flip
   `GOA_REQUESTS_PER_DAY=1000` on the Cloud Run job; widen `query_plan` keywords/NAICS;
   lock the public console behind IAM when the demo phase ends
4. **Swarm Industrialization**: Stand up orchestration layer to generate remaining 81 agents across 4 tenants
5. **Config Registry**: Build vendor-neutral runtime engine that resolves `(tenant_id, agent_id)` → `(model, tools, guardrails)`

---

**Last Updated**: 2026-07-03  
**Architecture Version**: Complete Architecture Document v2.2

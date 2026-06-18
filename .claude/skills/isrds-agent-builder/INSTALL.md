# Install: isrds-agent-builder

A Claude Code skill that builds one ISRDS agent as the six portable artifacts **plus
deployable Google ADK Python code** through the 8-phase pipeline (Architecture v2.2).
Swarm-compatible: hand-built agents emit identical artifact formats and code shapes
to what the swarm will later generate.

## Install (Claude Code)
Drop the `isrds-agent-builder/` folder into your project's skills directory:

    <your-repo>/.claude/skills/isrds-agent-builder/

(or `~/.claude/skills/` for a user-level install). It loads automatically when a request
matches the description in SKILL.md.

## Use
Just state a requirement, e.g.:
  "Build the Customer Satisfaction Survey Agent — event-driven on verified closeout,
   Review on survey-content change, Flag detractors to Order Management. No Approve gates."

The skill walks all 8 phases:
1. Generates the 6 portable YAML/JSON/MD artifacts into `agents/<agent_id>/`
2. Runs the quality gate (`scripts/quality_gate.py`)
3. Generates deployable ADK Python code into `agents/<agent_id>/adk/`
4. Tells you how to test locally and deploy to Vertex AI

### Test locally
```bash
cd agents/<agent_id>/adk
cp .env.template .env        # fill in your credentials
pip install -r requirements.txt
adk web .                    # opens http://localhost:8000
```

### Deploy to Vertex AI
```bash
adk deploy --project $GOOGLE_CLOUD_PROJECT --location us-central1
```

## Quality Gate Checks (Phase 6)

| # | Check | What it catches |
|---|-------|-----------------|
| 1 | Files exist | Missing artifacts from the six-file set |
| 2 | No vendor names | Hardcoded model/vendor tokens (`claude`, `gemini`, `gpt`, etc.) |
| 3 | Tools registered | Spec references a tool id not in tool-registry.yaml |
| 4 | Irreversible→Approve | `irreversible:true` actions without an Approve gate |
| 5 | Gate SLAs | Non-Approve gates missing supervisor/SLA; Approve with a timeout |
| 6 | Memory placement | Fields lacking `destination_justification` |
| 7 | Authority level | Missing or invalid `spec.authority` enum |
| 8 | Build posture | Invalid ABSORB posture missing required fields |
| 9 | Trust Ledger | Missing `trust-ledger-audit` invariant in governance |

## What's inside
- `SKILL.md` ............. orchestrator: the 8 phases, the no-vendor rule, the build loop
- `references/` ......... 11 docs: one per artifact + gates, build-sequencing, deploy,
                          build posture, authority + trust ledger, MCP server setup
- `templates/` .......... starter files for each of the six artifacts
- `templates/adk/` ...... starter ADK Python code (agent.py, governance.py, __init__.py)
- `examples/pmo-agent/` . complete quality-gate-passing build with ADK code
- `scripts/quality_gate.py` . Phase 6 automated checks (9 validators)

# Phase 8: Runtime Deploy (Google ADK + Vertex AI Agent Engine)

Deploy only after Phases 6 (quality gate) and 7 (human review) pass. Deploy is the
step where the six portable artifacts become a running agent.

## What the skill generates (build folder layout after Phase 8)

```
agents/<agent_id>/
├── agent-spec.yaml          # Artifact 1 (portable spec)
├── prompt.md                # Artifact 2 (agent instructions)
├── tool-registry.yaml       # Artifact 3 (tool definitions)
├── memory-schema.json       # Artifact 4 (memory placement)
├── governance-rules.yaml    # Artifact 5 (Layer 5 rules)
├── workflow-definition.yaml # Artifact 6 (triggers, gates, steps)
├── swarm-requirements.md    # friction log
└── adk/                     # ← DEPLOYABLE CODE (generated from artifacts)
    ├── __init__.py           # ADK entry point — exports root_agent
    ├── agent.py              # LlmAgent with MCPToolset connections
    ├── governance.py         # Layer 5 + Layer 6 enforcement at runtime
    ├── requirements.txt      # google-adk, google-cloud-aiplatform, pyyaml
    └── .env.template         # environment variables (never commit secrets)
```

The `adk/` folder is the deployable unit. It reads `prompt.md` and `governance-rules.yaml`
from the parent directory at runtime — the portable artifacts stay unchanged.

## How to deploy

### Prerequisites
```bash
# 1. Install Google Cloud SDK + ADK
brew install google-cloud-sdk
pip install google-adk

# 2. Authenticate
gcloud auth login
gcloud auth application-default login

# 3. Enable APIs
gcloud services enable aiplatform.googleapis.com
gcloud services enable cloudscheduler.googleapis.com
gcloud services enable secretmanager.googleapis.com
```

### Local testing (fastest path)
```bash
cd agents/<agent_id>/adk
cp .env.template .env        # fill in real values
adk web .                    # opens http://localhost:8000 with chat UI
```

The ADK web UI lets you chat with the agent, see tool calls, inspect the Trust Ledger,
and validate governance gates — all locally before any cloud deploy.

### Deploy to Vertex AI Agent Engine
```bash
cd agents/<agent_id>/adk
adk deploy --project $GOOGLE_CLOUD_PROJECT --location us-central1
```

### Set up Cloud Scheduler (for scheduled triggers)
```bash
# Hourly run
gcloud scheduler jobs create http <agent_id>-hourly \
  --schedule="0 * * * *" \
  --uri="https://<AGENT_ENDPOINT>/run" \
  --http-method=POST \
  --time-zone="America/Chicago" \
  --oidc-service-account-email=<SA>@<PROJECT>.iam.gserviceaccount.com
```

## What lands where (runtime mapping)

| Artifact | Runtime Layer |
|----------|--------------|
| `agent-spec.yaml` + `prompt.md` | ADK `LlmAgent` instruction (Layer 2) |
| `tool-registry.yaml` | `MCPToolset` connections per MCP server (Layer 3) |
| `memory-schema.json` | Vertex AI Agent Engine session memory (Layer 4) |
| `governance-rules.yaml` | `governance.py` reads and enforces at runtime (Layer 5) |
| gate block in workflow | `governance_gate` tool — agent pauses for human (Layer 6) |
| `workflow-definition.yaml` schedule | Cloud Scheduler cron job (Layer 7) |
| `workflow-definition.yaml` events | Pub/Sub subscription (Layer 7) |
| (telemetry) | GCP Cloud Observability (Layer 8) |

The runtime engine is resolved from `AGENT_MODEL` environment variable. In production,
`config_registry.resolve(tenant_id, agent_id)` sets this. Nothing is hardcoded.

## Observation period (the real release gate)

Run the agent **5–7 business days minimum** against real operational data before any
tenant-facing release. Watch the Layer 8 telemetry:

- Cost per run — first real data for the post-Feb-2026 Agent Engine cost model.
- Latency and token counts — real measurements, not estimates.
- Tool-call success rates — especially for MCP servers.
- Gate behavior — did Review/Escalate/Approve fire when expected?
- Trust Ledger — every decision and tool call recorded (FR-7).

## MCP server setup (per tool)

Each tool in `tool-registry.yaml` with `integration: mcp` needs an MCP server connection.
Two options:

**Option A: Managed MCP endpoint (recommended)**
Atlassian Rovo provides `https://mcp.atlassian.com/v1/mcp/authv2` — zero infrastructure.
Set `JIRA_MCP_URL` and `JIRA_MCP_TOKEN` in `.env`.

**Option B: Custom MCP server on Cloud Run**
Build a Python FastMCP server, containerize it, deploy to Cloud Run.
Store credentials in Secret Manager. See `references/10-mcp-server-setup.md`.

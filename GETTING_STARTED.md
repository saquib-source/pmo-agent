# Getting Started with PMO-Swarm

Welcome! This guide walks you through understanding PMO-Swarm and deploying it to production.

## What is PMO-Swarm?

**PMO-Swarm** is an autonomous multi-agent system that handles project management office (PMO) tasks:

- **Scans** Jira for stalled tickets (no activity >24 hours)
- **Audits** teams for ownership gaps and RACI violations
- **Checks** for data hygiene (missing fields, stale status)
- **Drafts** escalation messages for critical blockers
- **Reports** findings in a daily Operating Brief

It runs on a schedule (every 60 minutes by default) on Google Cloud Run, powered by Google's Vertex AI Agent Engine.

## Key Concepts

### 1. Multi-Agent Orchestration

One **orchestrator agent** coordinates five **skill agents**:

```
PMO Orchestrator
├── 1️⃣ Execution Tracking Agent → scans Jira for stalled tickets
├── 2️⃣ Follow-Up Agent → drafts escalation messages
├── 3️⃣ Ownership/RACI Agent → audits missing owners
├── 4️⃣ Feature Completeness Agent → tracks build progress
└── 5️⃣ Hygiene Agent → detects field violations
```

Each agent runs in parallel, queries different data sources, and reports findings to the orchestrator, which synthesizes everything into an **Operating Brief**.

### 2. Governance Gates

Not all agents have the same authority:

- **DECIDE_AND_REPORT** (read-only agents): Log findings, no approval needed
  - Execution Tracking, Feature Completeness, Hygiene
  
- **MUST_ESCALATE** (write agents): Open an approval gate before writing to Jira
  - Follow-Up (Review gate: "should I post this message?")
  - Ownership/RACI (Approve gate: "should I assign this ticket?")

Gates are logged to Cloud Logging for human review.

### 3. Data Persistence

Each cycle writes to four storage layers:

| Layer | Purpose | Query |
|-------|---------|-------|
| **Local Files** | Append-only backup (trust-ledger.jsonl, briefs/) | Read files locally |
| **Cloud Logging** | Real-time searchable, structured logs, traces | `gcloud logging read ...` |
| **BigQuery** | Analytics, system of record for briefs | `SELECT * FROM isrds_pmo.operating_briefs` |
| **AlloyDB** | Fast operational reads, 30-day backup | `SELECT * FROM daily_briefings` |

---

## Quick Start: Deploy in 5 Steps

### Prerequisites

- GCP Project: `isr-division-systems-488723`
- Service Account: `pmo-agent-svc@isr-division-systems-488723.iam.gserviceaccount.com`
- APIs enabled: Cloud Run, Vertex AI, Cloud Logging, BigQuery, AlloyDB
- Docker installed locally
- `gcloud` CLI installed and authenticated

### Step 1: Build the Docker Image

```bash
cd agents/pmo-swarm
docker build -t pmo-agent:latest .
```

**What happens**: Packages Python 3.12, all dependencies (google-adk, asyncpg, etc.), and agent code into a container.

### Step 2: Push to GCP Artifact Registry

```bash
# Authenticate Docker
gcloud auth configure-docker us-central1-docker.pkg.dev

# Tag image
docker tag pmo-agent:latest \
  us-central1-docker.pkg.dev/isr-division-systems-488723/docker-repo/pmo-agent:latest

# Push
docker push us-central1-docker.pkg.dev/isr-division-systems-488723/docker-repo/pmo-agent:latest
```

**What happens**: Stores the image in GCP's Artifact Registry. Cloud Run will pull from here.

### Step 3: Create Cloud Run Job

```bash
gcloud run jobs create pmo-swarm \
  --image=us-central1-docker.pkg.dev/isr-division-systems-488723/docker-repo/pmo-agent:latest \
  --project=isr-division-systems-488723 \
  --region=us-central1 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=isr-division-systems-488723" \
  --service-account=pmo-agent-svc@isr-division-systems-488723.iam.gserviceaccount.com \
  --timeout=3600
```

**What happens**: Defines a Cloud Run job. This is a one-time setup. The job will pull the image and run one cycle, then exit.

### Step 4: Execute the Job

```bash
gcloud run jobs execute pmo-swarm \
  --region=us-central1 \
  --project=isr-division-systems-488723
```

**What happens**: Starts a Cloud Run job execution. You can monitor logs in real-time.

### Step 5: Stream Logs

```bash
gcloud logging read \
  "resource.type=cloud_run_job AND resource.labels.job_name=pmo-swarm" \
  --project=isr-division-systems-488723 \
  --stream
```

**What happens**: Watches live logs. You'll see:
- `Startup complete` → config loaded, databases connected
- `Active Agents:` → list of 5 agents
- `PMO SWARM CYCLE START` → orchestrator running
- `Brief saved` → cycle completed
- `Container called exit(0)` → job finished successfully

---

## What You'll See in the Logs

Each cycle produces structured logs with emoji indicators:

```
================================================================================
🚀 PMO SWARM CYCLE START — 2026-06-24 20:24:22 UTC
================================================================================
📋 Mode: full
🏗️  Projects: ISRDS, ASHS
📊 Config: scan_interval=60min | brief_hour=8:00 | auto_comment=true
────────────────────────────────────────────────────────────────────────────
📌 Active Agents:
   1️⃣  execution_tracking_agent (DECIDE_AND_REPORT)
   2️⃣  follow_up_agent (MUST_ESCALATE)
   3️⃣  ownership_raci_agent (MUST_ESCALATE)
   4️⃣  feature_completeness_agent (DECIDE_AND_REPORT)
   5️⃣  hygiene_agent (DECIDE_AND_REPORT)
────────────────────────────────────────────────────────────────────────────

[agents execute in parallel...]

✅ Brief saved → briefs/brief_20260624_202422.txt
⏱️  Execution time: 45.23 seconds
📊 Operating Brief logged to BigQuery
📈 Cycle metrics logged to BigQuery

================================================================================
✨ PMO SWARM CYCLE COMPLETE — 45.23s total
================================================================================
```

---

## Health Checks: What to Verify

After your first cycle, verify:

- ✅ Cloud Run job shows exit code `0` (success)
- ✅ Logs show "Startup complete — projects: ISRDS, ASHS"
- ✅ All 5 agents listed in logs
- ✅ "Brief saved" to local file
- ✅ BigQuery `isrds_pmo.operating_briefs` table has new rows
- ✅ AlloyDB `daily_briefings` table updated
- ✅ "Container called exit(0)" at the end

**If any checks fail**, see Troubleshooting section below.

---

## Set Up Automated Scheduling (Optional)

To run PMO-Swarm every 60 minutes automatically:

```bash
gcloud scheduler jobs create app-engine pmo-scheduler \
  --schedule="*/60 * * * *" \
  --http-method=POST \
  --uri="https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/projects/isr-division-systems-488723/locations/us-central1/jobs/pmo-swarm:run" \
  --oidc-service-account-email=pmo-agent-svc@isr-division-systems-488723.iam.gserviceaccount.com \
  --oidc-token-audience="https://us-central1-run.googleapis.com"
```

This creates a Cloud Scheduler job that fires `pmo-swarm` every hour. Check Cloud Scheduler in GCP Console to verify.

---

## Monitoring & Queries

### View the Latest Operating Brief

```bash
# In BigQuery
SELECT cycle_ts, mode, brief_text, duration_ms
FROM `isr-division-systems-488723.isrds_pmo.operating_briefs`
ORDER BY cycle_ts DESC
LIMIT 1;
```

### Check Governance Gates Opened

```bash
# In Cloud Logging
gcloud logging read \
  "textPayload=~'governance_gate_opened'" \
  --project=isr-division-systems-488723 \
  --limit=20
```

### Query Cycle Metrics

```bash
# In BigQuery — how long did each cycle take?
SELECT cycle_ts, mode, duration_ms, stalled_ticket_count
FROM `isr-division-systems-488723.isrds_pmo.cycle_metrics`
ORDER BY cycle_ts DESC
LIMIT 10;
```

### Verify AlloyDB Backup

```bash
gcloud sql connect pmo-brief-db --user=postgres

# In the SQL prompt:
SELECT briefing_date, LENGTH(summary_text) as brief_len, generated_by_role
FROM daily_briefings
ORDER BY briefing_date DESC
LIMIT 10;
```

---

## Troubleshooting

### Issue: Cloud Run job times out

**Symptom**: Logs show timeout after 3600 seconds

**Fix**: Reduce SCAN_INTERVAL in config_registry, or optimize Jira JQL queries in agent prompts. Or increase timeout: `--timeout=7200`

### Issue: "Container called exit(0)" but no data in BigQuery

**Status**: ✅ Normal — AlloyDB and local files still have data

**Fix**: Check BigQuery dataset/table permissions
```bash
gcloud projects get-iam-policy isr-division-systems-488723 \
  --flatten="bindings[].members" \
  --filter="bindings.members:pmo-agent-svc*"
```

### Issue: Governance gates show "pending" but no one can approve

**Fix**: Wire gates to Slack or PagerDuty (see DEPLOY.md for details). Currently gates surface in Cloud Logging only.

### Issue: Jira queries fail ("Authentication required")

**Fix**: Verify JIRA_API_TOKEN and JIRA_USER_EMAIL in Cloud Run environment variables
```bash
gcloud run jobs update pmo-swarm \
  --update-env-vars="JIRA_API_TOKEN=your-token-here"
```

---

## Next Steps

1. **Read the Architecture**: [ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md) for the 8-layer model and data flow
2. **Deep Dive on Deployment**: [DEPLOY.md](agents/pmo-swarm/DEPLOY.md) for detailed setup, APIs, secrets, troubleshooting
3. **Wire Governance Gates**: Implement human approval flow (Slack, PagerDuty, custom dashboard)
4. **Set Up Alerts**: Create Cloud Monitoring alerts on governance escalations and stall counts
5. **Add More Agents**: Scale PMO-Swarm with agents for budget, risk, capacity planning
6. **Build a Dashboard**: Visualize briefs, stall trends, governance backlog in Looker or Cloud Console

---

## Architecture Overview

```
Cloud Scheduler (every 60 min)
         ↓
   Cloud Run Job
         ↓
   pmo_daemon.py (startup)
         ↓
   ADK Runner (Layer 2)
         ↓
   PMO Orchestrator (root agent)
         ↓
  ┌──┬──┬──┬──┬──┐
  ├──┴──┼──┴──┼──┤
  ↓     ↓     ↓     ↓     ↓
  [5 skill agents run in parallel]
     ↓         ↓         ↓
  Local    Cloud      BigQuery
  Files    Logging    AlloyDB
```

For a detailed diagram, see [ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md).

---

## Files Reference

| File | Purpose |
|------|---------|
| [pmo-summary.html](pmo-summary.html) | Visual guide (open in browser) |
| [ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md) | Technical architecture (8 layers, data flow, governance) |
| [DEPLOY.md](agents/pmo-swarm/DEPLOY.md) | Deployment guide (step-by-step, APIs, troubleshooting) |
| [agent_spec.yaml](agents/pmo-swarm/adk/agent_spec.yaml) | Agent specification |
| [pmo_daemon.py](agents/pmo-swarm/adk/pmo_daemon.py) | Main daemon script |
| [orchestrator.py](agents/pmo-swarm/adk/orchestrator.py) | Root agent implementation |
| [Dockerfile](agents/pmo-swarm/Dockerfile) | Container definition |

---

## Questions?

Check the docs:
1. **"How does it work?"** → [ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md)
2. **"How do I deploy it?"** → [DEPLOY.md](agents/pmo-swarm/DEPLOY.md)
3. **"What am I looking at in the logs?"** → [pmo-summary.html](pmo-summary.html) (Logging & Monitoring section)
4. **"How do I monitor it?"** → This guide (Monitoring & Queries section) + [ARCHITECTURE.md](agents/pmo-swarm/ARCHITECTURE.md) (Quick Reference section)

---

## Project Info

- **Author**: Mohd Saquib (saquib@isrdsystems.com)
- **Platform**: ISRDS Agentic Platform (v2.2)
- **Technology**: Google ADK, Vertex AI Agent Engine, Cloud Run
- **GCP Project**: `isr-division-systems-488723`
- **Repository**: [github.com/saquib-source/pmo-agent](https://github.com/saquib-source/pmo-agent)

Last Updated: June 24, 2026

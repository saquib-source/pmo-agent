# PMO Agent Production Deployment

## What is PMO-Swarm?

**PMO-Swarm** is a multi-agent orchestrator that automates project management office (PMO) operations:

### Architecture: 1 Orchestrator + 5 Skill Agents

**Root Agent: PMO Orchestrator**
- Routes requests to skill agents
- Enforces human approval gates before Jira writes
- Synthesizes findings into Operating Briefs
- Logs all decisions to Trust Ledger

**5 Skill Agents** (run in parallel via ADK swarm):
1. **Execution Tracking Agent** → Scans Jira for stalled tickets (>24h no activity)
2. **Follow-Up Agent** → Drafts escalation messages for stalled work (authority: MUST_ESCALATE)
3. **Ownership/RACI Agent** → Audits missing owners or role gaps (authority: MUST_ESCALATE)
4. **Feature Completeness Agent** → Tracks build progress across feature catalog
5. **Hygiene Agent** → Detects missing fields, stale status, validation violations

### How PMO-Swarm Works

1. **Scheduled Cycle** (every `SCAN_INTERVAL` minutes)
   - Daemon starts a new session with the orchestrator
   - Orchestrator fans out prompts to 5 skill agents
   - Each agent queries Jira, Firestore, or AlloyDB
   - Agents report findings back to orchestrator

2. **Governance Gates**
   - Read-only agents (execution, feature, hygiene): log findings, no approval needed
   - Write agents (follow-up, RACI): open a "Review" or "Approve" gate
   - Gates are surfaced in Cloud Logging for human review

3. **Data Flow**
   - **Trust Ledger** (adk/trust-ledger.jsonl): every decision logged locally
   - **Cloud Logging** (isrds-pmo-swarm): all governance gates + metrics
   - **BigQuery** (isrds_pmo dataset): Operating Briefs + cycle KPIs
   - **AlloyDB** (daily_briefings table): daily brief backup for fast reads

4. **Operating Brief Output**
   - Synthesized summary of all agent findings
   - Saved to local `briefs/` directory
   - Written to BigQuery + AlloyDB daily
   - Ready to send to leadership (or trigger Slack notifier)

---

## Prerequisites

- **GCP Project**: `isr-division-systems-488723`
- **Service Account**: `pmo-agent-svc@isr-division-systems-488723.iam.gserviceaccount.com`
- **GCP APIs Enabled**:
  - Cloud Run
  - Cloud Logging
  - Cloud Monitoring
  - Vertex AI Agent Engine (ADK)
  - BigQuery
  - AlloyDB
  - Cloud Build (for CI/CD)
- **Environment**:
  - Python 3.12+
  - Docker (for local testing and deployment)

---

## Deployment Steps

### Step 1: Build & Test Locally

```bash
cd agents/pmo-swarm/adk

# Install dependencies
pip install -r requirements.txt

# Set environment variables (see .env.template)
cp .env.template .env
# Edit .env with real credentials:
#   GOOGLE_CLOUD_PROJECT=isr-division-systems-488723
#   GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
#   JIRA_HOST=your-tenant.atlassian.net
#   JIRA_API_TOKEN=...
#   JIRA_USER_EMAIL=...

# Run a single cycle
python pmo_daemon.py --once

# Watch logs
tail -f logs/pmo_daemon.log
```

### Step 2: Build Docker Image

```bash
cd agents/pmo-swarm

# Build locally
docker build -t pmo-agent:latest .

# Test locally
docker run \
  -e GOOGLE_CLOUD_PROJECT=isr-division-systems-488723 \
  -e GOOGLE_APPLICATION_CREDENTIALS=/app/service-account.json \
  -v /path/to/service-account.json:/app/service-account.json \
  pmo-agent:latest
```

### Step 3: Push to GCP Artifact Registry

```bash
# Configure Docker for GCP
gcloud auth configure-docker us-central1-docker.pkg.dev

# Tag image
docker tag pmo-agent:latest \
  us-central1-docker.pkg.dev/isr-division-systems-488723/docker-repo/pmo-agent:latest

# Push
docker push us-central1-docker.pkg.dev/isr-division-systems-488723/docker-repo/pmo-agent:latest
```

### Step 4: Deploy to Cloud Run (One-off Job)

```bash
gcloud run jobs create pmo-swarm \
  --image=us-central1-docker.pkg.dev/isr-division-systems-488723/docker-repo/pmo-agent:latest \
  --project=isr-division-systems-488723 \
  --region=us-central1 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=isr-division-systems-488723,SCAN_INTERVAL=60" \
  --service-account=pmo-agent-svc@isr-division-systems-488723.iam.gserviceaccount.com \
  --timeout=3600
```

### Step 5: Execute & Monitor

```bash
# Run job
gcloud run jobs execute pmo-swarm \
  --region=us-central1 \
  --project=isr-division-systems-488723

# Stream logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=pmo-swarm" \
  --project=isr-division-systems-488723 \
  --limit=100 \
  --stream
```

---

## Monitoring & Debugging

### View Logs in Cloud Logging

```bash
# All PMO swarm logs
gcloud logging read "logName=projects/isr-division-systems-488723/logs/isrds-pmo-swarm" \
  --project=isr-division-systems-488723 \
  --limit=100

# Governance gates only
gcloud logging read "textPayload=~'governance_gate'" \
  --project=isr-division-systems-488723 \
  --limit=50
```

### Query BigQuery

```sql
-- Latest Operating Briefs
SELECT cycle_ts, mode, brief_text, duration_ms
FROM `isr-division-systems-488723.isrds_pmo.operating_briefs`
ORDER BY cycle_ts DESC
LIMIT 10;

-- Cycle metrics
SELECT cycle_ts, mode, duration_ms, stalled_ticket_count, governance_gates_triggered
FROM `isr-division-systems-488723.isrds_pmo.cycle_metrics`
ORDER BY cycle_ts DESC
LIMIT 10;
```

### Check AlloyDB Backup

```bash
# Connect to AlloyDB instance
gcloud sql connect pmo-brief-db --user=postgres

# Query daily briefings
SELECT briefing_date, summary_text, generated_by_role
FROM daily_briefings
ORDER BY briefing_date DESC
LIMIT 10;
```

### Local Trust Ledger

```bash
# View decisions + gates logged locally
cat adk/trust-ledger.jsonl | jq .

# Count by type
cat adk/trust-ledger.jsonl | jq -s 'group_by(.type) | map({type: .[0].type, count: length})'
```

---

## Common Issues & Fixes

### Issue: "Container called exit(0)" appears in logs

**Status**: ✅ Normal — means job completed successfully

### Issue: Governance gates show "pending" but no reviewer set

**Fix**: Add human review integration (e.g., Slack notifier, email, dashboard UI)

```python
# In orchestrator.py, call:
slack_notify_gate(gate_type, description, ticket_key)
```

### Issue: BigQuery write fails, brief still saved locally

**Status**: ✅ By design — AlloyDB + local files are backups

**Fix**: Check BigQuery dataset + table permissions
```bash
gcloud projects get-iam-policy isr-division-systems-488723 \
  --flatten="bindings[].members" \
  --filter="bindings.members:pmo-agent-svc*"
```

### Issue: Jira queries timeout

**Fix**: Reduce `SCAN_INTERVAL` in config_registry, or optimize JQL queries in agent prompts

---

## Next: Production Hardening

1. **Scale agents** → Add more skill agents (e.g., budget tracking, risk assessment)
2. **Human gates** → Wire approval gates to Slack / PagerDuty / custom dashboard
3. **Alerts** → Alert on governance escalations, high stall counts, feature gaps
4. **Retention** → Archive briefs to Cloud Storage after 30 days
5. **CI/CD** → Wire GitHub Actions → Artifact Registry → Cloud Run (push-button deploy)

---

## Commands Quick Reference

```bash
# Deploy
gcloud run jobs create pmo-swarm \
  --image=us-central1-docker.pkg.dev/isr-division-systems-488723/docker-repo/pmo-agent:latest \
  --project=isr-division-systems-488723 \
  --region=us-central1

# Execute
gcloud run jobs execute pmo-swarm --region=us-central1 --project=isr-division-systems-488723

# Stream logs
gcloud logging read "resource.type=cloud_run_job AND resource.labels.job_name=pmo-swarm" \
  --project=isr-division-systems-488723 --stream

# Update
gcloud run jobs update pmo-swarm \
  --image=us-central1-docker.pkg.dev/isr-division-systems-488723/docker-repo/pmo-agent:latest \
  --region=us-central1 --project=isr-division-systems-488723
```

---

**Last Updated**: 2026-06-24  
**Author**: Mohd Saquib  
**Architecture**: ISRDS Complete Architecture Document v2.2

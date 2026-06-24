#!/bin/bash
# =============================================================================
# Deploy PMO Swarm to Google Cloud Run (Job)
# Run from: agents/pmo-swarm/
#
# This matches the LIVE deployment:
#   - Image:    gcr.io/<project>/pmo-swarm:latest   (built via Cloud Build — no local Docker)
#   - Database: Cloud SQL Postgres instance `tier3`  (via Cloud SQL Auth Proxy)
#   - Secrets:  jira-api-token, alloydb-password      (existing Secret Manager secrets)
#   - SA:       swarm-558@<project>.iam.gserviceaccount.com
#   - Job:      pmo-swarm  (region us-central1)
#
# Prerequisites:
#   gcloud auth login
#   gcloud config set project isr-division-systems-488723
#
# Usage:
#   ./deploy.sh            # build image (Cloud Build) + redeploy the Cloud Run Job
#   ./deploy.sh --image    # build + push image only
#   ./deploy.sh --job      # redeploy the Cloud Run Job only (uses existing :latest)
#   ./deploy.sh --run      # build + redeploy + execute one cycle
# =============================================================================

set -euo pipefail

# ── Variables ─────────────────────────────────────────────────────────────────
PROJECT="${GOOGLE_CLOUD_PROJECT:-isr-division-systems-488723}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SA_EMAIL="swarm-558@${PROJECT}.iam.gserviceaccount.com"
IMAGE="gcr.io/${PROJECT}/pmo-swarm"
JOB_NAME="pmo-swarm"
CLOUD_SQL_INSTANCE="${CLOUD_SQL_INSTANCE:-${PROJECT}:${REGION}:tier3}"

# Colours
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
step() { echo -e "\n${CYAN}==> $1${NC}"; }
ok()   { echo -e "${GREEN}    ✓ $1${NC}"; }
warn() { echo -e "${YELLOW}    ⚠ $1${NC}"; }

# ── Parse flags ───────────────────────────────────────────────────────────────
DO_IMAGE=true; DO_JOB=true; DO_RUN=false
for arg in "$@"; do
  case $arg in
    --image) DO_JOB=false ;;
    --job)   DO_IMAGE=false ;;
    --run)   DO_RUN=true ;;
  esac
done

# ── Auth ────────────────────────────────────────────────────────────────────
step "Verify gcloud auth"
gcloud config set project "$PROJECT" --quiet
ACTIVE=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1)
[[ -z "$ACTIVE" ]] && { echo "Not authenticated. Run: gcloud auth login"; exit 1; }
ok "Authenticated as: $ACTIVE  project: $PROJECT"

# ── Build + push image via Cloud Build (no local Docker needed) ───────────────
if $DO_IMAGE; then
  step "Build + push image via Cloud Build → ${IMAGE}:latest"
  gcloud builds submit --tag="${IMAGE}:latest" --project="$PROJECT" .
  ok "Image pushed: ${IMAGE}:latest"
fi

# ── Deploy / update the Cloud Run Job ─────────────────────────────────────────
if $DO_JOB; then
  step "Deploy Cloud Run Job: $JOB_NAME"

  # Env vars. DB uses Cloud SQL Postgres via the Auth Proxy (CLOUD_SQL_INSTANCE).
  # CLOUD_SQL_* are the canonical names; the code still accepts legacy ALLOYDB_* too.
  ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT}"
  ENV_VARS+=",GOOGLE_CLOUD_LOCATION=${REGION}"
  ENV_VARS+=",GOOGLE_GENAI_USE_VERTEXAI=TRUE"
  ENV_VARS+=",AGENT_MODEL=gemini-2.5-flash"
  ENV_VARS+=",JIRA_URL=https://lixillabs.atlassian.net"
  ENV_VARS+=",JIRA_EMAIL=saquib@isrdsystems.com"
  ENV_VARS+=",JIRA_PROJECT=ISRDS"
  ENV_VARS+=",JIRA_PROJECTS=ISRDS"
  ENV_VARS+=",TENANT_ID=isrds"
  ENV_VARS+=",CLOUD_SQL_INSTANCE=${CLOUD_SQL_INSTANCE}"
  ENV_VARS+=",CLOUD_SQL_DATABASE=isrds_agentic"
  ENV_VARS+=",CLOUD_SQL_USER=postgres"
  ENV_VARS+=",TRUST_LEDGER_PATH=trust-ledger.jsonl"
  ENV_VARS+=",OBSERVABILITY_ENABLED=true"
  ENV_VARS+=",PMO_SCAN_INTERVAL_MINUTES=60"
  ENV_VARS+=",PMO_STALE_THRESHOLD_HOURS=24"
  ENV_VARS+=",PMO_CHASE_THRESHOLD_HOURS=48"
  ENV_VARS+=",PMO_ESCALATE_THRESHOLD_HOURS=72"
  ENV_VARS+=",PMO_BRIEF_HOUR=7"
  ENV_VARS+=",PMO_AUTO_COMMENT=${PMO_AUTO_COMMENT:-false}"
  ENV_VARS+=",LOG_LEVEL=${LOG_LEVEL:-INFO}"

  gcloud run jobs deploy "$JOB_NAME" \
    --image="${IMAGE}:latest" \
    --region="$REGION" \
    --project="$PROJECT" \
    --service-account="$SA_EMAIL" \
    --set-cloudsql-instances="$CLOUD_SQL_INSTANCE" \
    --task-timeout="30m" \
    --max-retries=1 \
    --set-env-vars="$ENV_VARS" \
    --set-secrets="JIRA_API_TOKEN=jira-api-token:latest,CLOUD_SQL_PASSWORD=alloydb-password:latest" \
    --quiet
  ok "Cloud Run Job deployed: $JOB_NAME"
fi

# ── Optionally run one cycle ──────────────────────────────────────────────────
if $DO_RUN; then
  step "Execute one cycle"
  gcloud run jobs execute "$JOB_NAME" --region="$REGION" --project="$PROJECT"
fi

echo ""
echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN}  PMO Swarm — done${NC}"
echo -e "${GREEN}=====================================================${NC}"
echo "  Job:     $JOB_NAME ($REGION)"
echo "  Image:   ${IMAGE}:latest"
echo "  DB:      Cloud SQL Postgres — $CLOUD_SQL_INSTANCE"
echo ""
echo "  Run one cycle:"
echo "    gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT"
echo ""
echo "  Find pending approvals in logs:"
echo "    gcloud logging read 'resource.type=cloud_run_job AND textPayload:PENDING_APPROVAL' \\"
echo "      --project=$PROJECT --limit=20 --format='value(textPayload)'"
echo ""

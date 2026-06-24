#!/bin/bash
# =============================================================================
# Deploy PMO Swarm to Google Cloud
# Run from: agents/pmo-swarm/
#
# Prerequisites:
#   gcloud auth login
#   gcloud auth application-default login
#   gcloud config set project isr-division-systems-488723
#
# Usage:
#   ./deploy.sh              # full deploy (all 9 steps)
#   ./deploy.sh --image      # rebuild and push image only (step 8)
#   ./deploy.sh --job        # redeploy Cloud Run Job only (step 9)
# =============================================================================

set -euo pipefail

# ── Variables ─────────────────────────────────────────────────────────────────
PROJECT="${GOOGLE_CLOUD_PROJECT:-isr-division-systems-488723}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SA_NAME="pmo-swarm"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
IMAGE_NAME="pmo-swarm"
AR_REPO="isrds-agents"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${AR_REPO}/${IMAGE_NAME}"
JOB_NAME="pmo-swarm-job"
SCHEDULER_JOB="pmo-swarm-trigger"
SCAN_INTERVAL_MIN="${PMO_SCAN_INTERVAL_MINUTES:-60}"

# Colours
GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
step() { echo -e "\n${CYAN}==> Step $1${NC}: $2"; }
ok()   { echo -e "${GREEN}    ✓ $1${NC}"; }
warn() { echo -e "${YELLOW}    ⚠ $1${NC}"; }


# ── Parse flags ───────────────────────────────────────────────────────────────
IMAGE_ONLY=false
JOB_ONLY=false
for arg in "$@"; do
  case $arg in
    --image) IMAGE_ONLY=true ;;
    --job)   JOB_ONLY=true ;;
  esac
done


# ── Step 1 — Verify auth ──────────────────────────────────────────────────────
step 1 "Verify gcloud auth"
gcloud config set project "$PROJECT" --quiet
ACTIVE=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1)
if [[ -z "$ACTIVE" ]]; then
  echo "Not authenticated. Run: gcloud auth login"
  exit 1
fi
ok "Authenticated as: $ACTIVE  project: $PROJECT"

if $IMAGE_ONLY; then
  echo "(--image flag: skipping to step 8)"
  goto_step=8
elif $JOB_ONLY; then
  echo "(--job flag: skipping to step 9)"
  goto_step=9
else
  goto_step=2
fi


# ── Step 2 — Enable APIs ──────────────────────────────────────────────────────
if [[ $goto_step -le 2 ]]; then
step 2 "Enable required GCP APIs"
gcloud services enable \
  alloydb.googleapis.com \
  aiplatform.googleapis.com \
  run.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  bigquery.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  cloudscheduler.googleapis.com \
  --project="$PROJECT" --quiet
ok "APIs enabled"
fi


# ── Step 3 — AlloyDB ──────────────────────────────────────────────────────────
if [[ $goto_step -le 3 ]]; then
step 3 "AlloyDB — create instance (if not exists)"
warn "AlloyDB requires VPC peering and takes ~5 min to provision."
warn "If isrds-agentic already exists from the survey agent deploy, skip this."
echo ""
echo "    To create a new AlloyDB cluster:"
echo "    gcloud alloydb clusters create isrds-cluster \\"
echo "      --region=$REGION --project=$PROJECT \\"
echo "      --password=\$ALLOYDB_PASSWORD"
echo ""
echo "    gcloud alloydb instances create isrds-primary \\"
echo "      --cluster=isrds-cluster --region=$REGION \\"
echo "      --instance-type=PRIMARY --cpu-count=2"
echo ""
echo "    Once running, get the IP:"
echo "    gcloud alloydb instances describe isrds-primary \\"
echo "      --cluster=isrds-cluster --region=$REGION \\"
echo "      --format='value(ipAddress)'"
echo ""
read -rp "    Press Enter once AlloyDB is ready (or Ctrl+C to stop here)..."
ok "Continuing — AlloyDB assumed ready"
fi


# ── Step 4 — Run migrations ───────────────────────────────────────────────────
if [[ $goto_step -le 4 ]]; then
step 4 "Run SQL migrations against AlloyDB"

if [[ -z "${ALLOYDB_HOST:-}" ]]; then
  warn "ALLOYDB_HOST not set. Set it and re-run, or run migrations manually:"
  echo ""
  echo "    export ALLOYDB_HOST=<ip>  ALLOYDB_USER=<user>"
  echo "    export ALLOYDB_PASSWORD=<pw>  ALLOYDB_DATABASE=isrds_agentic"
  echo ""
  echo "    PGPASSWORD=\$ALLOYDB_PASSWORD psql -h \$ALLOYDB_HOST \\"
  echo "      -U \$ALLOYDB_USER -d \$ALLOYDB_DATABASE \\"
  echo "      -f ../../migrations/001_foundation.sql \\"
  echo "      -f ../../migrations/002_survey.sql \\"
  echo "      -f ../../migrations/003_seed_config_registry.sql \\"
  echo "      -f ../../migrations/004_pmo_swarm.sql"
  warn "Skipping migrations — set ALLOYDB_HOST to run automatically."
else
  PGPASSWORD="$ALLOYDB_PASSWORD" psql \
    -h "$ALLOYDB_HOST" -U "$ALLOYDB_USER" -d "${ALLOYDB_DATABASE:-isrds_agentic}" \
    -f "../../migrations/001_foundation.sql" \
    -f "../../migrations/002_survey.sql" \
    -f "../../migrations/003_seed_config_registry.sql" \
    -f "../../migrations/004_pmo_swarm.sql"
  ok "Migrations applied"
fi
fi


# ── Step 5 — Service account + IAM ───────────────────────────────────────────
if [[ $goto_step -le 5 ]]; then
step 5 "Service account + IAM roles"
if ! gcloud iam service-accounts describe "$SA_EMAIL" \
     --project="$PROJECT" &>/dev/null; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="PMO Swarm Agent" \
    --project="$PROJECT"
  ok "Created service account: $SA_EMAIL"
else
  ok "Service account already exists: $SA_EMAIL"
fi

for ROLE in \
  "roles/alloydb.client" \
  "roles/aiplatform.user" \
  "roles/logging.logWriter" \
  "roles/monitoring.metricWriter" \
  "roles/bigquery.dataEditor" \
  "roles/secretmanager.secretAccessor" \
  "roles/run.invoker"; do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:$SA_EMAIL" \
    --role="$ROLE" --quiet 2>/dev/null || true
done
ok "IAM roles bound"
fi


# ── Step 6 — Secret Manager ───────────────────────────────────────────────────
if [[ $goto_step -le 6 ]]; then
step 6 "Store secrets in Secret Manager"

_upsert_secret() {
  local NAME=$1 VALUE=$2
  if gcloud secrets describe "$NAME" --project="$PROJECT" &>/dev/null; then
    echo "$VALUE" | gcloud secrets versions add "$NAME" --data-file=- --project="$PROJECT" --quiet
  else
    echo "$VALUE" | gcloud secrets create "$NAME" --data-file=- \
      --replication-policy=automatic --project="$PROJECT" --quiet
  fi
}

: "${JIRA_API_TOKEN:?Set JIRA_API_TOKEN before running deploy}"
: "${ALLOYDB_PASSWORD:?Set ALLOYDB_PASSWORD before running deploy}"

_upsert_secret "pmo-jira-api-token"   "$JIRA_API_TOKEN"
_upsert_secret "pmo-alloydb-password" "$ALLOYDB_PASSWORD"

ok "Secrets stored in Secret Manager"
fi


# ── Step 7 — Vertex AI Agent Engine ───────────────────────────────────────────
if [[ $goto_step -le 7 ]]; then
step 7 "Vertex AI Agent Engine (persistent session memory)"

EXISTING_ENGINE=$(gcloud ai reasoning-engines list \
  --region="$REGION" --project="$PROJECT" \
  --filter="displayName=pmo-swarm-sessions" \
  --format="value(name)" 2>/dev/null | head -1)

if [[ -n "$EXISTING_ENGINE" ]]; then
  ENGINE_ID=$(basename "$EXISTING_ENGINE")
  ok "Existing engine found: $ENGINE_ID"
else
  warn "Creating Vertex AI reasoning engine (this may take ~2 min)..."
  ENGINE_FULL=$(gcloud ai reasoning-engines create \
    --display-name="pmo-swarm-sessions" \
    --region="$REGION" --project="$PROJECT" \
    --format="value(name)" 2>/dev/null || echo "")
  if [[ -n "$ENGINE_FULL" ]]; then
    ENGINE_ID=$(basename "$ENGINE_FULL")
    ok "Created engine: $ENGINE_ID"
  else
    warn "Could not create engine automatically."
    warn "Create it manually and set VERTEX_AGENT_ENGINE_ID in Secret Manager."
    ENGINE_ID=""
  fi
fi

if [[ -n "$ENGINE_ID" ]]; then
  echo "$ENGINE_ID" | gcloud secrets versions add "pmo-vertex-engine-id" \
    --data-file=- --project="$PROJECT" --quiet 2>/dev/null || \
  echo "$ENGINE_ID" | gcloud secrets create "pmo-vertex-engine-id" \
    --data-file=- --replication-policy=automatic --project="$PROJECT" --quiet
  ok "VERTEX_AGENT_ENGINE_ID=$ENGINE_ID stored in Secret Manager"
fi
fi


# ── Step 8 — Build + push Docker image ────────────────────────────────────────
if [[ $goto_step -le 8 ]]; then
step 8 "Build and push Docker image to Artifact Registry"

# Create Artifact Registry repo if it doesn't exist
if ! gcloud artifacts repositories describe "$AR_REPO" \
     --location="$REGION" --project="$PROJECT" &>/dev/null; then
  gcloud artifacts repositories create "$AR_REPO" \
    --repository-format=docker \
    --location="$REGION" \
    --project="$PROJECT" \
    --description="ISRDS agent container images"
  ok "Created Artifact Registry repo: $AR_REPO"
fi

gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet

# Tag with git SHA + latest
GIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "local")
docker build \
  --tag "${IMAGE}:${GIT_SHA}" \
  --tag "${IMAGE}:latest" \
  --file Dockerfile \
  .

docker push "${IMAGE}:${GIT_SHA}"
docker push "${IMAGE}:latest"
ok "Image pushed: ${IMAGE}:${GIT_SHA}"
fi


# ── Step 9 — Deploy Cloud Run Job + Cloud Scheduler ──────────────────────────
if [[ $goto_step -le 9 ]]; then
step 9 "Deploy Cloud Run Job + Cloud Scheduler trigger"

ALLOYDB_HOST_VAL="${ALLOYDB_HOST:-}"
JIRA_URL_VAL="${JIRA_URL:-https://lixillabs.atlassian.net}"
JIRA_EMAIL_VAL="${JIRA_EMAIL:-you@isrdsystems.com}"

# Create or update the Cloud Run Job
gcloud run jobs deploy "$JOB_NAME" \
  --image="${IMAGE}:latest" \
  --region="$REGION" \
  --project="$PROJECT" \
  --service-account="$SA_EMAIL" \
  --task-timeout="30m" \
  --max-retries=1 \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},TENANT_ID=ashs,AGENT_MODEL=gemini-2.5-flash,JIRA_URL=${JIRA_URL_VAL},JIRA_EMAIL=${JIRA_EMAIL_VAL},JIRA_PROJECTS=ASHS\,BAS\,BTK\,FQ\,ISRDS\,MDP\,SOC\,UNCS,ALLOYDB_HOST=${ALLOYDB_HOST_VAL},ALLOYDB_PORT=5432,ALLOYDB_DATABASE=isrds_agentic,ALLOYDB_USER=${ALLOYDB_USER:-pmo_agent},OBSERVABILITY_ENABLED=true,PMO_AUTO_COMMENT=false,PMO_BRIEF_HOUR=7" \
  --set-secrets="JIRA_API_TOKEN=pmo-jira-api-token:latest,ALLOYDB_PASSWORD=pmo-alloydb-password:latest,VERTEX_AGENT_ENGINE_ID=pmo-vertex-engine-id:latest" \
  --quiet

ok "Cloud Run Job deployed: $JOB_NAME"

# Cloud Scheduler — trigger job every N minutes
SCHEDULE="*/${SCAN_INTERVAL_MIN} * * * *"

if gcloud scheduler jobs describe "$SCHEDULER_JOB" \
   --location="$REGION" --project="$PROJECT" &>/dev/null; then
  gcloud scheduler jobs update http "$SCHEDULER_JOB" \
    --schedule="$SCHEDULE" \
    --location="$REGION" --project="$PROJECT" --quiet
  ok "Cloud Scheduler job updated: every ${SCAN_INTERVAL_MIN} min"
else
  gcloud scheduler jobs create http "$SCHEDULER_JOB" \
    --schedule="$SCHEDULE" \
    --uri="https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT}/jobs/${JOB_NAME}:run" \
    --oauth-service-account-email="$SA_EMAIL" \
    --location="$REGION" --project="$PROJECT"
  ok "Cloud Scheduler job created: every ${SCAN_INTERVAL_MIN} min"
fi
fi


# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN}  PMO Swarm deployed successfully${NC}"
echo -e "${GREEN}=====================================================${NC}"
echo ""
echo "  Cloud Run Job:   $JOB_NAME"
echo "  Schedule:        every ${SCAN_INTERVAL_MIN} minutes"
echo "  Image:           ${IMAGE}:latest"
echo "  Project:         $PROJECT / $REGION"
echo ""
echo "  Run one cycle manually:"
echo "  gcloud run jobs execute $JOB_NAME --region=$REGION --project=$PROJECT"
echo ""
echo "  View logs:"
echo "  gcloud logging read 'logName=\"projects/${PROJECT}/logs/isrds%2Fpmo-swarm\"' \\"
echo "    --project=$PROJECT --limit=50 --format='table(timestamp,jsonPayload.agent_id,jsonPayload.event_type,jsonPayload.detail)'"
echo ""

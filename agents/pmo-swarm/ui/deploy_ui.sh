#!/bin/bash
# =============================================================================
# Deploy the PMO Swarm Control UI (Cloud Run Service)
# Run from: agents/pmo-swarm/   (NOT from ui/ — it bundles ../adk)
#
#   ./ui/deploy_ui.sh
#
# Produces a public URL where a non-developer can review briefs, see the swarm,
# read the Trust Ledger, approve/decline pending actions, and trigger a cycle.
# =============================================================================
set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-isr-division-systems-488723}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SA_EMAIL="swarm-558@${PROJECT}.iam.gserviceaccount.com"
IMAGE="gcr.io/${PROJECT}/pmo-swarm-ui"
SERVICE="pmo-swarm-ui"
CLOUD_SQL_INSTANCE="${CLOUD_SQL_INSTANCE:-${PROJECT}:${REGION}:tier3}"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
step(){ echo -e "\n${CYAN}==> $1${NC}"; }
ok(){ echo -e "${GREEN}    ✓ $1${NC}"; }

gcloud config set project "$PROJECT" --quiet

step "Build UI image via Cloud Build → ${IMAGE}:latest"
gcloud builds submit --project="$PROJECT" \
  --config=ui/cloudbuild.yaml \
  --substitutions=_IMAGE="${IMAGE}:latest" \
  .
ok "UI image pushed"

step "Deploy Cloud Run Service: $SERVICE"
gcloud run deploy "$SERVICE" \
  --image="${IMAGE}:latest" \
  --region="$REGION" --project="$PROJECT" \
  --service-account="$SA_EMAIL" \
  --set-cloudsql-instances="$CLOUD_SQL_INSTANCE" \
  --set-env-vars="GOOGLE_CLOUD_PROJECT=${PROJECT},GOOGLE_CLOUD_LOCATION=${REGION},PMO_JOB_NAME=pmo-swarm,CLOUD_SQL_INSTANCE=${CLOUD_SQL_INSTANCE},CLOUD_SQL_DATABASE=isrds_agentic,CLOUD_SQL_USER=postgres,TENANT_ID=isrds,JIRA_URL=https://lixillabs.atlassian.net,JIRA_EMAIL=saquib@isrdsystems.com" \
  --set-secrets="JIRA_API_TOKEN=jira-api-token:latest,CLOUD_SQL_PASSWORD=alloydb-password:latest" \
  --allow-unauthenticated \
  --memory=512Mi --cpu=1 --port=8080 \
  --quiet

URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --project="$PROJECT" --format='value(status.url)')
# Let the UI's service account trigger the Job
gcloud run jobs add-iam-policy-binding pmo-swarm \
  --region="$REGION" --project="$PROJECT" \
  --member="serviceAccount:${SA_EMAIL}" --role="roles/run.invoker" --quiet 2>/dev/null || true

echo ""
echo -e "${GREEN}=====================================================${NC}"
echo -e "${GREEN}  PMO Swarm Control UI deployed${NC}"
echo -e "${GREEN}  $URL${NC}"
echo -e "${GREEN}=====================================================${NC}"

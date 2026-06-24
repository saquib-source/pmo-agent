#!/bin/bash
# =============================================================================
# Deploy Survey Agent to Vertex AI Agent Engine
# Run from: agents/
# Prerequisites: gcloud auth login, gcloud config set project isrds-agentic-prod
# =============================================================================

set -euo pipefail

PROJECT="${GOOGLE_CLOUD_PROJECT:-isrds-agentic-prod}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
AGENT_NAME="isrds-survey-agent"

echo "==> Installing dependencies..."
pip install -r requirements.txt --quiet

echo "==> Running SQL migrations against AlloyDB..."
# Run via Cloud SQL Auth Proxy or from within VPC
# psql "host=$ALLOYDB_HOST dbname=$ALLOYDB_DATABASE user=$ALLOYDB_USER" \
#   -f ../migrations/001_foundation.sql \
#   -f ../migrations/002_survey.sql \
#   -f ../migrations/003_seed_config_registry.sql
echo "    (Run migrations manually via AlloyDB Studio or psql — see migrations/ folder)"

echo "==> Enabling Vertex AI Agent Engine API..."
gcloud services enable aiplatform.googleapis.com --project="$PROJECT"

echo "==> Creating Vertex AI Agent Engine resource (if not exists)..."
# The ADK deploy command registers your agent with Vertex AI Agent Engine
# This wraps your agent in a managed session service
adk deploy \
  --project="$PROJECT" \
  --region="$REGION" \
  --agent-name="$AGENT_NAME" \
  survey_agent/agent.py

echo ""
echo "==> Deployment complete."
echo "    Agent name:    $AGENT_NAME"
echo "    Project:       $PROJECT"
echo "    Region:        $REGION"
echo ""
echo "==> Next: copy the Agent Engine resource ID into .env as VERTEX_AI_AGENT_ENGINE_ID"

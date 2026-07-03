#!/usr/bin/env bash
# Serve the GOA Events API + review console locally against the real Cloud SQL.
# Loads .env, then runs uvicorn. Open http://127.0.0.1:8080/
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No .env — copy .env.template to .env and fill SAM_GOV_API_KEY + ANTHROPIC_API_KEY"; exit 1; }
set -a; . ./.env; set +a
exec .venv/bin/uvicorn goa.events.http_app:app --host 127.0.0.1 --port "${PORT:-8080}"

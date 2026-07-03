#!/usr/bin/env bash
# Run a real backfill for a source (default sam_gov) against real SAM.gov + Cloud SQL.
# Usage: scripts/run_backfill.sh [source_id]
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No .env — copy .env.template to .env and fill the keys"; exit 1; }
set -a; . ./.env; set +a
exec .venv/bin/python -m jobs.backfill --source "${1:-sam_gov}"

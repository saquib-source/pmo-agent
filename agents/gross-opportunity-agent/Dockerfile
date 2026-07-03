# Gross Opportunity Agent — Events API service + jobs runner.
# One image; CMD serves the Events API (Cloud Run service). The same image runs the
# jobs (backfill/delta/expiration_sweep/seed/scout) by overriding the command, e.g.
#   gcloud run jobs create goa-backfill --image ... --command python --args -m,jobs.backfill,--source,sam_gov
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps: none required beyond the wheels (asyncpg, grpcio ship manylinux wheels).
COPY requirements.txt .
# anthropic[vertex] pulls google-auth (already pinned); install the extra explicitly.
RUN pip install -r requirements.txt "anthropic[vertex]==0.115.1" "uvicorn[standard]==0.49.0"

COPY goa/ ./goa/
COPY jobs/ ./jobs/
COPY config/ ./config/
COPY screen/ ./screen/
COPY sql/ ./sql/

# Cloud Run provides $PORT (default 8080). Serve the Events API.
ENV PORT=8080
CMD exec uvicorn goa.events.http_app:app --host 0.0.0.0 --port ${PORT}

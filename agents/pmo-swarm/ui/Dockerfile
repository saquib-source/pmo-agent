# PMO Swarm Control UI — Cloud Run Service
# Built from the pmo-swarm/ directory so it can bundle the shared `adk` modules.
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      gcc libpq-dev ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# UI deps + the agent deps (UI reuses adk.shared.*)
COPY ui/requirements.txt ./ui-requirements.txt
COPY adk/requirements.txt ./adk-requirements.txt
RUN pip install --no-cache-dir -r ui-requirements.txt -r adk-requirements.txt

# Bundle the agent code (UI imports adk.shared.governance / jira_client / db)
COPY adk/ adk/
COPY ui/  ui/
COPY governance-rules.yaml ./

ENV PYTHONUNBUFFERED=1
ENV PORT=8080

# Cloud Run sets $PORT; uvicorn binds to it.
CMD ["sh", "-c", "uvicorn ui.server:app --host 0.0.0.0 --port ${PORT:-8080}"]

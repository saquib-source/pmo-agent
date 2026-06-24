"""Fetch recent Cloud Run Job logs from Cloud Logging."""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from google.oauth2 import service_account
from googleapiclient import discovery

PROJECT = "isr-division-systems-488723"
creds = service_account.Credentials.from_service_account_file(
    "adk/service-account.json",
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

logging_svc = discovery.build("logging", "v2", credentials=creds, cache_discovery=False)

body = {
    "resourceNames": [f"projects/{PROJECT}"],
    "filter": (
        'resource.type="cloud_run_job" '
        'resource.labels.job_name="pmo-swarm" '
    ),
    "orderBy": "timestamp desc",
    "pageSize": 200,
}

resp = logging_svc.entries().list(body=body).execute()
entries = resp.get("entries", [])
print(f"Total log entries: {len(entries)}\n")

for entry in reversed(entries):
    ts = entry.get("timestamp", "")[:23]
    severity = entry.get("severity", "DEFAULT")[:5]
    payload = entry.get("textPayload") or str(entry.get("jsonPayload", {}).get("message", entry.get("jsonPayload", "")))
    marker = "***" if severity in ("ERROR", "CRITI") else ("WRN" if severity == "WARNI" else "   ")
    # Only print non-trivial lines
    if payload and payload.strip():
        print(f"{marker} {ts} [{severity}] {payload[:300]}")

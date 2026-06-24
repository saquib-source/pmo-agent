"""Phase 1: Enable APIs + store secrets in Secret Manager."""
import io, os, sys, time, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

SA_KEY   = "adk/service-account.json"
PROJECT  = "isr-division-systems-488723"

APIS = [
    "alloydb.googleapis.com",
    "aiplatform.googleapis.com",
    "bigquery.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "run.googleapis.com",
    "cloudbuild.googleapis.com",
    "secretmanager.googleapis.com",
    "vpcaccess.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudresourcemanager.googleapis.com",
    "iam.googleapis.com",
]

# Secret values are read from the environment — never hardcode credentials.
#   export JIRA_API_TOKEN=...  ALLOYDB_PASSWORD=...
SECRETS = {
    "jira-api-token":   os.environ.get("JIRA_API_TOKEN", ""),
    "alloydb-password": os.environ.get("ALLOYDB_PASSWORD", ""),
}

from google.oauth2 import service_account
from googleapiclient import discovery

creds = service_account.Credentials.from_service_account_file(
    SA_KEY, scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
print(f"[AUTH] swarm-558 loaded OK")

# ── Enable APIs ───────────────────────────────────────────────────────────────
print("\n[1] Enabling APIs...")
svc = discovery.build("serviceusage", "v1", credentials=creds, cache_discovery=False)
for api in APIS:
    name = f"projects/{PROJECT}/services/{api}"
    try:
        resp = svc.services().enable(name=name).execute()
        op = resp.get("name", "")
        if "operations" in op:
            for _ in range(40):
                result = svc.operations().get(name=op).execute()
                if result.get("done"):
                    break
                time.sleep(3)
        print(f"  OK  {api}")
    except Exception as e:
        msg = str(e)
        if "already enabled" in msg.lower() or "409" in msg:
            print(f"  --  {api} (already enabled)")
        else:
            print(f"  ERR {api}: {msg[:100]}")

# ── Secret Manager ────────────────────────────────────────────────────────────
print("\n[2] Storing secrets...")
from google.cloud import secretmanager as sm_lib
sm = sm_lib.SecretManagerServiceClient(credentials=creds)
parent = f"projects/{PROJECT}"

for name, value in SECRETS.items():
    path = f"{parent}/secrets/{name}"
    try:
        sm.create_secret(request={
            "parent": parent,
            "secret_id": name,
            "secret": {"replication": {"automatic": {}}},
        })
        print(f"  created secret: {name}")
    except Exception:
        print(f"  exists: {name}")
    try:
        sm.add_secret_version(request={
            "parent": path,
            "payload": {"data": value.encode()},
        })
        print(f"  version added: {name}")
    except Exception as e:
        print(f"  ERR version {name}: {e}")

print("\n[Phase 1 complete]")

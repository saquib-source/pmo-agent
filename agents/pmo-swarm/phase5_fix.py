"""
Phase 5: Fix DB (Unix socket via Cloud SQL proxy volume), fix session creation.
Changes:
  - db.py: use asyncpg Unix socket via /cloudsql/{instance} (no Python connector lib)
  - pmo_daemon.py: check get_session return value (None), not exception
  - requirements.txt: remove cloud-sql-python-connector (not needed)
  - Cloud Run Job: add cloudSqlInstance volume + volumeMount
"""
import io, sys, os, time, tarfile, tempfile
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
from google.oauth2 import service_account
from googleapiclient import discovery
from google.cloud import storage

PROJECT   = "isr-division-systems-488723"
REGION    = "us-central1"
SA_EMAIL  = "swarm-558@isr-division-systems-488723.iam.gserviceaccount.com"
IMAGE_TAG = f"gcr.io/{PROJECT}/pmo-swarm:latest"
JOB_NAME  = "pmo-swarm"
INSTANCE  = "isr-division-systems-488723:us-central1:tier3"
BUCKET    = f"{PROJECT}-cloudbuild-src"

creds = service_account.Credentials.from_service_account_file(
    "adk/service-account.json",
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

# ─── Step 1: Archive ──────────────────────────────────────────────────────────
print("[1] Creating source archive...")
SRC_DIR = Path(".")
SKIP = {
    ".venv", "__pycache__", ".git",
    "adk/service-account.json", "adk/.env", "adk/trust-ledger.jsonl",
    "adk/logs", "adk/briefs",
    "phase1_apis_secrets.py", "phase2_db.py", "phase3_build_deploy.py",
    "phase4_rebuild.py", "phase5_fix.py",
    "provision_gcp.py", "provision_results.json",
    "check_apis.py", "find_db.py", "authorize_and_migrate.py",
    "check_execution.py", "fetch_logs.py",
}

def excluded(rel_str):
    parts = rel_str.replace("\\", "/").lstrip("./").split("/")
    for p in parts:
        if p in {"__pycache__", ".venv", ".git"}:
            return True
    normed = rel_str.replace("\\", "/").lstrip("./")
    for s in SKIP:
        if normed == s.lstrip("./") or normed.startswith(s.rstrip("/") + "/"):
            return True
    return False

archive = Path(tempfile.gettempdir()) / "pmo-swarm-v3.tar.gz"
n = 0
with tarfile.open(archive, "w:gz") as tar:
    for item in SRC_DIR.rglob("*"):
        rel = str(item.relative_to(SRC_DIR))
        if not excluded(rel) and item.is_file():
            tar.add(item, arcname=rel)
            n += 1
print(f"  {n} files, {archive.stat().st_size//1024} KB")

# ─── Step 2: Upload ───────────────────────────────────────────────────────────
print("\n[2] Uploading to GCS...")
gcs = storage.Client(project=PROJECT, credentials=creds)
gcs.bucket(BUCKET).blob("pmo-swarm-v3.tar.gz").upload_from_filename(str(archive))
print(f"  gs://{BUCKET}/pmo-swarm-v3.tar.gz")

# ─── Step 3: Cloud Build ─────────────────────────────────────────────────────
print("\n[3] Cloud Build...")
cb = discovery.build("cloudbuild", "v1", credentials=creds, cache_discovery=False)
resp = cb.projects().builds().create(projectId=PROJECT, body={
    "source": {"storageSource": {"bucket": BUCKET, "object": "pmo-swarm-v3.tar.gz"}},
    "steps": [{"name": "gcr.io/cloud-builders/docker",
               "args": ["build", "-t", IMAGE_TAG, "-f", "Dockerfile", "."]}],
    "images": [IMAGE_TAG],
    "options": {"logging": "CLOUD_LOGGING_ONLY", "machineType": "E2_HIGHCPU_8"},
    "timeout": "1200s",
}).execute()
bid = resp["metadata"]["build"]["id"]
print(f"  Build: {bid}")
for i in range(120):
    time.sleep(10)
    s = cb.projects().builds().get(projectId=PROJECT, id=bid).execute().get("status")
    if i % 6 == 0: print(f"  [{i*10}s] {s}")
    if s == "SUCCESS": print("  SUCCESS"); break
    elif s in ("FAILURE","CANCELLED","TIMEOUT","INTERNAL_ERROR"): sys.exit(1)

# ─── Step 4: Update Cloud Run Job ────────────────────────────────────────────
print("\n[4] Updating Cloud Run Job (add Cloud SQL volume, minimal resources)...")
run_svc = discovery.build("run", "v2", credentials=creds, cache_discovery=False)

env_vars = [
    {"name": "GOOGLE_CLOUD_PROJECT",         "value": PROJECT},
    {"name": "GOOGLE_CLOUD_LOCATION",        "value": REGION},
    {"name": "GOOGLE_GENAI_USE_VERTEXAI",    "value": "TRUE"},
    {"name": "AGENT_MODEL",                  "value": "gemini-2.5-flash"},
    {"name": "JIRA_URL",                     "value": "https://lixillabs.atlassian.net"},
    {"name": "JIRA_EMAIL",                   "value": "saquib@isrdsystems.com"},
    {"name": "JIRA_PROJECT",                 "value": "ISRDS"},
    {"name": "JIRA_PROJECTS",                "value": "ISRDS"},
    {"name": "TENANT_ID",                    "value": "isrds"},
    {"name": "CLOUD_SQL_INSTANCE",           "value": INSTANCE},
    {"name": "ALLOYDB_DATABASE",             "value": "isrds_agentic"},
    {"name": "ALLOYDB_USER",                 "value": "postgres"},
    {"name": "TRUST_LEDGER_PATH",            "value": "trust-ledger.jsonl"},
    {"name": "OBSERVABILITY_ENABLED",        "value": "true"},
    {"name": "PMO_SCAN_INTERVAL_MINUTES",    "value": "60"},
    {"name": "PMO_STALE_THRESHOLD_HOURS",    "value": "24"},
    {"name": "PMO_CHASE_THRESHOLD_HOURS",    "value": "48"},
    {"name": "PMO_ESCALATE_THRESHOLD_HOURS", "value": "72"},
    {"name": "PMO_BRIEF_HOUR",               "value": "7"},
    {"name": "PMO_AUTO_COMMENT",             "value": "false"},
]
secret_env = [
    {"name": "JIRA_API_TOKEN",   "valueSource": {"secretKeyRef": {"secret": "jira-api-token",   "version": "latest"}}},
    {"name": "ALLOYDB_PASSWORD", "valueSource": {"secretKeyRef": {"secret": "alloydb-password", "version": "latest"}}},
]

job_body = {
    "template": {
        "taskCount": 1,
        "template": {
            "serviceAccount": SA_EMAIL,
            "maxRetries": 0,
            "timeout": "1800s",
            "containers": [{
                "image": IMAGE_TAG,
                "env": env_vars + secret_env,
                "resources": {"limits": {"cpu": "1", "memory": "512Mi"}},
                "volumeMounts": [{"name": "cloudsql", "mountPath": "/cloudsql"}],
            }],
            "volumes": [{
                "name": "cloudsql",
                "cloudSqlInstance": {"instances": [INSTANCE]},
            }],
        },
    },
}

job_path = f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB_NAME}"
op = run_svc.projects().locations().jobs().patch(name=job_path, body=job_body).execute()
op_name = op.get("name", "")
for _ in range(30):
    time.sleep(5)
    r = run_svc.projects().locations().operations().get(name=op_name).execute()
    if r.get("done"):
        if r.get("error"): print(f"  ERROR: {r['error']}"); sys.exit(1)
        print(f"  Job updated: Cloud SQL volume + 1 CPU / 512Mi")
        break

# ─── Step 5: Test run ─────────────────────────────────────────────────────────
print("\n[5] Triggering test run...")
er = run_svc.projects().locations().jobs().run(name=job_path, body={}).execute()
eid = er.get("metadata", {}).get("name", "").split("/")[-1]
print(f"  Execution: {eid}")
exec_path = f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB_NAME}/executions/{eid}"
print("  Polling (up to 6 min)...")
for i in range(36):
    time.sleep(10)
    ei = run_svc.projects().locations().jobs().executions().get(name=exec_path).execute()
    succ, fail, run = ei.get("succeededCount",0), ei.get("failedCount",0), ei.get("runningCount",0)
    comp = ei.get("completionTime","")
    if comp or succ > 0:
        print(f"\n  SUCCEEDED ({i*10}s)")
        break
    if fail > 0 and run == 0:
        print(f"\n  FAILED — check logs: https://console.cloud.google.com/logs/query?project={PROJECT}")
        break
    if i % 3 == 0:
        print(f"  [{i*10}s] succeeded={succ} failed={fail} running={run}")

print("\n" + "="*55)
print("FIXES DEPLOYED")
print(f"  DB: Unix socket /cloudsql/{INSTANCE}")
print(f"  Session: create_session now called when get_session returns None")
print(f"  Log name: isrds-pmo-swarm (no slash)")
print(f"  Projects: ISRDS only")
print(f"  Resources: 1 CPU / 512Mi")
print("="*55)

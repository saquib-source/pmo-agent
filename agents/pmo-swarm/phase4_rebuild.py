"""
Phase 4: Rebuild image with fixes, update Cloud Run Job (minimal resources + ISRDS-only).
Fixes in this build:
  - db.py: Cloud SQL Connector for Cloud Run (no authorized-network juggling)
  - observability.py: log name isrds/pmo-swarm -> isrds-pmo-swarm (no slash)
  - pmo_daemon.py: create ADK session before run_async (SessionNotFoundError fix)
  - requirements.txt: add cloud-sql-python-connector[asyncpg]
  - Cloud Run Job: 1 CPU / 512Mi (was 2/2Gi), JIRA_PROJECTS=ISRDS (was all 8)
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

# ─── Step 1: Package source ───────────────────────────────────────────────────
print("[1] Creating source archive...")
SRC_DIR = Path(".")
EXCLUDE_PREFIXES = {
    ".venv", "adk/.venv", "__pycache__", ".git",
    "adk/service-account.json", "adk/.env",
    "adk/trust-ledger.jsonl", "adk/logs", "adk/briefs",
}
EXCLUDE_SCRIPTS = {
    "phase1_apis_secrets.py", "phase2_db.py", "phase3_build_deploy.py",
    "phase4_rebuild.py", "provision_gcp.py", "provision_results.json",
    "check_apis.py", "find_db.py", "authorize_and_migrate.py",
    "check_execution.py", "fetch_logs.py",
}

def should_exclude(rel_str):
    parts = rel_str.replace("\\", "/").lstrip("./").split("/")
    for part in parts:
        if part in {"__pycache__", ".venv", ".git"}:
            return True
    normed = rel_str.replace("\\", "/").lstrip("./")
    for p in EXCLUDE_PREFIXES:
        if normed.startswith(p.lstrip("./")):
            return True
    if normed in EXCLUDE_SCRIPTS:
        return True
    return False

archive_path = Path(tempfile.gettempdir()) / "pmo-swarm-src-v2.tar.gz"
count = 0
with tarfile.open(archive_path, "w:gz") as tar:
    for item in SRC_DIR.rglob("*"):
        rel = item.relative_to(SRC_DIR)
        rel_str = str(rel)
        if should_exclude(rel_str):
            continue
        if item.is_file():
            tar.add(item, arcname=rel_str)
            count += 1

size_kb = archive_path.stat().st_size // 1024
print(f"  {count} files, {size_kb} KB -> {archive_path}")

# ─── Step 2: Upload to GCS ────────────────────────────────────────────────────
print("\n[2] Uploading to GCS...")
gcs = storage.Client(project=PROJECT, credentials=creds)
bucket = gcs.bucket(BUCKET)
blob = bucket.blob("pmo-swarm-src-v2.tar.gz")
blob.upload_from_filename(str(archive_path))
print(f"  gs://{BUCKET}/pmo-swarm-src-v2.tar.gz")

# ─── Step 3: Cloud Build ─────────────────────────────────────────────────────
print("\n[3] Submitting Cloud Build...")
cb = discovery.build("cloudbuild", "v1", credentials=creds, cache_discovery=False)
build_config = {
    "source": {"storageSource": {"bucket": BUCKET, "object": "pmo-swarm-src-v2.tar.gz"}},
    "steps": [{"name": "gcr.io/cloud-builders/docker",
               "args": ["build", "-t", IMAGE_TAG, "-f", "Dockerfile", "."]}],
    "images": [IMAGE_TAG],
    "options": {"logging": "CLOUD_LOGGING_ONLY", "machineType": "E2_HIGHCPU_8"},
    "timeout": "1200s",
}
resp = cb.projects().builds().create(projectId=PROJECT, body=build_config).execute()
build_id = resp["metadata"]["build"]["id"]
print(f"  Build ID: {build_id}")

for i in range(120):
    time.sleep(10)
    info = cb.projects().builds().get(projectId=PROJECT, id=build_id).execute()
    status = info.get("status")
    if i % 6 == 0:
        print(f"  [{i*10}s] {status}")
    if status == "SUCCESS":
        print(f"  SUCCESS: {IMAGE_TAG}")
        break
    elif status in ("FAILURE", "CANCELLED", "TIMEOUT", "INTERNAL_ERROR"):
        print(f"  BUILD {status}")
        sys.exit(1)
else:
    print("  Build timed out"); sys.exit(1)

# ─── Step 4: Update Cloud Run Job ────────────────────────────────────────────
print("\n[4] Updating Cloud Run Job (minimal resources, ISRDS-only)...")
run_svc = discovery.build("run", "v2", credentials=creds, cache_discovery=False)

env_vars = [
    {"name": "GOOGLE_CLOUD_PROJECT",         "value": PROJECT},
    {"name": "GOOGLE_CLOUD_LOCATION",        "value": REGION},
    {"name": "GOOGLE_GENAI_USE_VERTEXAI",    "value": "TRUE"},
    {"name": "AGENT_MODEL",                  "value": "gemini-2.5-flash"},
    {"name": "JIRA_URL",                     "value": "https://lixillabs.atlassian.net"},
    {"name": "JIRA_EMAIL",                   "value": "saquib@isrdsystems.com"},
    {"name": "JIRA_PROJECT",                 "value": "ISRDS"},
    {"name": "JIRA_PROJECTS",                "value": "ISRDS"},     # internal only
    {"name": "TENANT_ID",                    "value": "isrds"},     # internal tenant
    {"name": "CLOUD_SQL_INSTANCE",           "value": INSTANCE},    # Cloud SQL Connector
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
secret_env_vars = [
    {"name": "JIRA_API_TOKEN",    "valueSource": {"secretKeyRef": {"secret": "jira-api-token",   "version": "latest"}}},
    {"name": "ALLOYDB_PASSWORD",  "valueSource": {"secretKeyRef": {"secret": "alloydb-password", "version": "latest"}}},
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
                "env": env_vars + secret_env_vars,
                "resources": {
                    "limits": {"cpu": "1", "memory": "512Mi"}
                },
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
        if r.get("error"):
            print(f"  ERROR: {r['error']}"); sys.exit(1)
        print(f"  Job updated: 1 CPU / 512Mi / ISRDS-only")
        break

# ─── Step 5: Add Cloud SQL IAM permission (cloudsql.instances.connect) ────────
print("\n[5] Verifying Cloud SQL Client role for swarm-558...")
rm = discovery.build("cloudresourcemanager", "v1", credentials=creds, cache_discovery=False)
policy = rm.projects().getIamPolicy(resource=PROJECT, body={}).execute()
bindings = policy.get("bindings", [])
member = f"serviceAccount:{SA_EMAIL}"
role = "roles/cloudsql.client"
existing = next((b for b in bindings if b["role"] == role), None)
if existing and member in existing.get("members", []):
    print(f"  Already has {role}")
else:
    if existing:
        existing["members"].append(member)
    else:
        bindings.append({"role": role, "members": [member]})
    policy["bindings"] = bindings
    rm.projects().setIamPolicy(resource=PROJECT, body={"policy": policy}).execute()
    print(f"  Granted {role} to swarm-558")

# ─── Step 6: Trigger test run ─────────────────────────────────────────────────
print("\n[6] Triggering test execution...")
exec_resp = run_svc.projects().locations().jobs().run(name=job_path, body={}).execute()
exec_meta = exec_resp.get("metadata", {})
exec_name_full = exec_meta.get("name", "")
exec_id = exec_name_full.split("/")[-1] if exec_name_full else "unknown"
print(f"  Execution: {exec_id}")
print(f"  Monitor: https://console.cloud.google.com/run/jobs/{JOB_NAME}/executions?project={PROJECT}")

# Poll result
print("  Waiting up to 6 minutes...")
exec_path = f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB_NAME}/executions/{exec_id}"
for i in range(36):
    time.sleep(10)
    ei = run_svc.projects().locations().jobs().executions().get(name=exec_path).execute()
    succeeded = ei.get("succeededCount", 0)
    failed = ei.get("failedCount", 0)
    running = ei.get("runningCount", 0)
    comp = ei.get("completionTime", "")
    if comp or succeeded > 0:
        print(f"\n  SUCCEEDED in {i*10}s")
        break
    if failed > 0 and running == 0:
        print(f"\n  FAILED after {i*10}s — check logs")
        break
    if i % 3 == 0:
        print(f"  [{i*10}s] succeeded={succeeded} failed={failed} running={running}")

print("\n" + "="*60)
print("REBUILD COMPLETE")
print(f"  Resources : 1 CPU / 512Mi (down from 2 / 2Gi)")
print(f"  Projects  : ISRDS only (internal; removed ASHS/BAS/BTK/FQ/MDP/SOC/UNCS)")
print(f"  DB        : Cloud SQL Connector (no IP authorisation needed)")
print(f"  Log name  : isrds-pmo-swarm (fixed slash)")
print(f"  Session   : created before run_async (fixed SessionNotFoundError)")
print("="*60)

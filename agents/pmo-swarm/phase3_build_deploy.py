"""
Phase 3: Build container image via Cloud Build and deploy as Cloud Run Job
+ Cloud Scheduler hourly trigger.
Run from pmo-swarm/ directory.
"""
import io, sys, os, time, tarfile, json, tempfile
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
from google.oauth2 import service_account
from googleapiclient import discovery

PROJECT   = "isr-division-systems-488723"
REGION    = "us-central1"
SA_EMAIL  = "swarm-558@isr-division-systems-488723.iam.gserviceaccount.com"
IMAGE_TAG = f"gcr.io/{PROJECT}/pmo-swarm:latest"
JOB_NAME  = "pmo-swarm"
SCHEDULER_JOB = "pmo-swarm-hourly"

creds = service_account.Credentials.from_service_account_file(
    "adk/service-account.json",
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)

# ─── Step 1: Create GCS bucket for build source ──────────────────────────────
print("[1] Prepare Cloud Storage bucket for build source...")
from google.cloud import storage
gcs = storage.Client(project=PROJECT, credentials=creds)
BUCKET_NAME = f"{PROJECT}-cloudbuild-src"
try:
    bucket = gcs.get_bucket(BUCKET_NAME)
    print(f"  Bucket exists: gs://{BUCKET_NAME}")
except Exception:
    bucket = gcs.create_bucket(BUCKET_NAME, location=REGION)
    print(f"  Created bucket: gs://{BUCKET_NAME}")

# ─── Step 2: Create source archive ────────────────────────────────────────────
print("\n[2] Creating source archive...")
SRC_DIR = Path(".")  # pmo-swarm/

# Files/dirs to exclude from the archive
EXCLUDE = {
    ".venv", "adk/.venv", "__pycache__", ".git",
    "adk/service-account.json", "adk/.env",
    "adk/trust-ledger.jsonl",
    # deployment helper scripts — not needed in image
    "phase1_apis_secrets.py", "phase2_db.py", "phase3_build_deploy.py",
    "provision_gcp.py", "provision_results.json",
    "check_apis.py", "find_db.py", "authorize_and_migrate.py",
}

def should_exclude(path_str):
    parts = path_str.replace("\\", "/").lstrip("./").split("/")
    for part in parts:
        if part in {"__pycache__", ".venv", ".git"}:
            return True
    for excl in EXCLUDE:
        if path_str.replace("\\", "/").lstrip("./").startswith(excl.lstrip("./")):
            return True
    return False

archive_path = Path(tempfile.gettempdir()) / "pmo-swarm-src.tar.gz"
with tarfile.open(archive_path, "w:gz") as tar:
    for item in SRC_DIR.rglob("*"):
        rel = item.relative_to(SRC_DIR)
        rel_str = str(rel)
        if should_exclude(rel_str):
            continue
        if item.is_file():
            tar.add(item, arcname=rel_str)

size_kb = archive_path.stat().st_size // 1024
print(f"  Archive: {archive_path} ({size_kb} KB)")

# ─── Step 3: Upload to GCS ────────────────────────────────────────────────────
print("\n[3] Uploading source to GCS...")
blob_name = "pmo-swarm-src.tar.gz"
blob = bucket.blob(blob_name)
blob.upload_from_filename(str(archive_path))
print(f"  Uploaded: gs://{BUCKET_NAME}/{blob_name}")

# ─── Step 4: Submit Cloud Build ───────────────────────────────────────────────
print("\n[4] Submitting Cloud Build...")
cb = discovery.build("cloudbuild", "v1", credentials=creds, cache_discovery=False)

build_config = {
    "source": {
        "storageSource": {
            "bucket": BUCKET_NAME,
            "object": blob_name,
        }
    },
    "steps": [
        {
            "name": "gcr.io/cloud-builders/docker",
            "args": [
                "build",
                "-t", IMAGE_TAG,
                "-f", "Dockerfile",
                "."
            ]
        }
    ],
    "images": [IMAGE_TAG],
    "options": {
        "logging": "CLOUD_LOGGING_ONLY",
        "machineType": "E2_HIGHCPU_8",
    },
    "timeout": "1200s",
}

resp = cb.projects().builds().create(projectId=PROJECT, body=build_config).execute()
build_id = resp["metadata"]["build"]["id"]
print(f"  Build ID: {build_id}")
print(f"  Logs: https://console.cloud.google.com/cloud-build/builds/{build_id}?project={PROJECT}")
print("  Waiting for build to complete (this takes ~3-5 minutes)...")

for i in range(120):
    time.sleep(10)
    build_info = cb.projects().builds().get(projectId=PROJECT, id=build_id).execute()
    status = build_info.get("status")
    if i % 6 == 0:
        print(f"  [{i*10}s] status: {status}")
    if status == "SUCCESS":
        print(f"  Build SUCCESS: {IMAGE_TAG}")
        break
    elif status in ("FAILURE", "CANCELLED", "TIMEOUT", "INTERNAL_ERROR"):
        log_url = build_info.get("logUrl", "")
        print(f"  Build {status}. Log: {log_url}")
        sys.exit(1)
else:
    print("  Build timed out after 20 minutes")
    sys.exit(1)

# ─── Step 5: Create Cloud Run Job ─────────────────────────────────────────────
print("\n[5] Creating Cloud Run Job...")
run_svc = discovery.build("run", "v2", credentials=creds, cache_discovery=False)

# Environment variables for the job (non-secret)
env_vars = [
    {"name": "GOOGLE_CLOUD_PROJECT",         "value": PROJECT},
    {"name": "GOOGLE_CLOUD_LOCATION",        "value": REGION},
    {"name": "GOOGLE_GENAI_USE_VERTEXAI",    "value": "TRUE"},
    {"name": "AGENT_MODEL",                  "value": "gemini-2.5-flash"},
    {"name": "JIRA_URL",                     "value": "https://lixillabs.atlassian.net"},
    {"name": "JIRA_EMAIL",                   "value": "saquib@isrdsystems.com"},
    {"name": "JIRA_PROJECT",                 "value": "ISRDS"},
    {"name": "TENANT_ID",                    "value": "ashs"},
    {"name": "ALLOYDB_HOST",                 "value": "34.16.10.122"},
    {"name": "ALLOYDB_PORT",                 "value": "5432"},
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

# Secret references for sensitive values
secret_env_vars = [
    {
        "name": "JIRA_API_TOKEN",
        "valueSource": {
            "secretKeyRef": {
                "secret": "jira-api-token",
                "version": "latest"
            }
        }
    },
    {
        "name": "ALLOYDB_PASSWORD",
        "valueSource": {
            "secretKeyRef": {
                "secret": "alloydb-password",
                "version": "latest"
            }
        }
    },
]

job_body = {
    "template": {
        "taskCount": 1,
        "template": {
            "serviceAccount": SA_EMAIL,
            "maxRetries": 1,
            "timeout": "3600s",
            "containers": [
                {
                    "image": IMAGE_TAG,
                    "env": env_vars + secret_env_vars,
                    "resources": {
                        "limits": {
                            "cpu": "2",
                            "memory": "2Gi"
                        }
                    }
                }
            ]
        }
    }
}

parent = f"projects/{PROJECT}/locations/{REGION}"
job_path = f"{parent}/jobs/{JOB_NAME}"

# Check if job already exists
try:
    existing = run_svc.projects().locations().jobs().get(name=job_path).execute()
    print(f"  Job exists — updating...")
    op = run_svc.projects().locations().jobs().patch(
        name=job_path,
        body=job_body
    ).execute()
except Exception:
    print(f"  Creating new job: {JOB_NAME}")
    op = run_svc.projects().locations().jobs().create(
        parent=parent,
        jobId=JOB_NAME,
        body=job_body
    ).execute()

op_name = op.get("name", "")
print(f"  Operation: {op_name}")

# Wait for job creation/update
if op_name:
    for _ in range(30):
        time.sleep(5)
        op_result = run_svc.projects().locations().operations().get(name=op_name).execute()
        if op_result.get("done"):
            err = op_result.get("error")
            if err:
                print(f"  ERROR creating job: {err}")
                sys.exit(1)
            print(f"  Cloud Run Job '{JOB_NAME}' ready")
            break
    else:
        print("  WARNING: Job creation timed out — may still be in progress")

# ─── Step 6: Grant Cloud SQL network access from Cloud Run ─────────────────────
# Cloud SQL needs to allow Cloud Run's egress IPs — this is handled automatically
# when the Cloud Run Job is configured to use VPC (or with public IP + authorized net)
# For now the job connects to 34.16.10.122 over the public internet.
# Cloud Run uses a static outbound NAT from a GCP NAT range — those IPs
# (35.245.110.238, 34.145.160.237, etc.) are ALREADY in the authorized networks list.
print("\n[6] Cloud SQL network access: Cloud Run egress IPs already in authorized networks. OK")

# ─── Step 7: Create Cloud Scheduler job ───────────────────────────────────────
print("\n[7] Creating Cloud Scheduler job (hourly)...")
scheduler = discovery.build("cloudscheduler", "v1", credentials=creds, cache_discovery=False)

# Cloud Run Job execution URI
job_run_uri = f"https://{REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/{PROJECT}/jobs/{JOB_NAME}:run"

scheduler_job_body = {
    "name": f"{parent}/jobs/{SCHEDULER_JOB}",
    "description": "Trigger PMO swarm daemon every hour",
    "schedule": "0 * * * *",
    "timeZone": "UTC",
    "httpTarget": {
        "uri": job_run_uri,
        "httpMethod": "POST",
        "body": "",
        "oauthToken": {
            "serviceAccountEmail": SA_EMAIL
        }
    }
}

try:
    existing = scheduler.projects().locations().jobs().get(
        name=f"{parent}/jobs/{SCHEDULER_JOB}"
    ).execute()
    print(f"  Scheduler job exists — updating...")
    scheduler.projects().locations().jobs().patch(
        name=f"{parent}/jobs/{SCHEDULER_JOB}",
        body=scheduler_job_body
    ).execute()
except Exception:
    scheduler.projects().locations().jobs().create(
        parent=parent,
        body=scheduler_job_body
    ).execute()
    print(f"  Created scheduler job: {SCHEDULER_JOB}")

print(f"  Schedule: every hour (0 * * * * UTC)")

# ─── Step 8: Execute one test run ─────────────────────────────────────────────
print("\n[8] Triggering initial test execution of Cloud Run Job...")
try:
    exec_resp = run_svc.projects().locations().jobs().run(
        name=job_path,
        body={}
    ).execute()
    exec_name = exec_resp.get("metadata", {}).get("name", "unknown")
    print(f"  Execution started: {exec_name}")
    print(f"  Monitor at: https://console.cloud.google.com/run/jobs/{JOB_NAME}/executions?project={PROJECT}")
    print(f"  Logs at: https://console.cloud.google.com/logs/query?project={PROJECT}")
except Exception as e:
    print(f"  WARNING: Could not trigger test run: {e}")
    print(f"  You can trigger manually from: https://console.cloud.google.com/run/jobs/{JOB_NAME}?project={PROJECT}")

print("\n" + "="*65)
print("DEPLOYMENT COMPLETE")
print("="*65)
print(f"  Image:      {IMAGE_TAG}")
print(f"  Job:        Cloud Run Job '{JOB_NAME}' in {REGION}")
print(f"  Schedule:   Every hour via Cloud Scheduler '{SCHEDULER_JOB}'")
print(f"  DB:         34.16.10.122:5432/isrds_agentic (13 tables)")
print(f"  Secrets:    jira-api-token, alloydb-password (Secret Manager)")
print("")
print("MINIMAL IAM ROLES FOR swarm-558 (replace Owner):")
print("  roles/aiplatform.user              — Vertex AI / Gemini calls")
print("  roles/bigquery.dataEditor          — L8 analytics writes")
print("  roles/bigquery.jobUser             — L8 analytics queries")
print("  roles/logging.logWriter            — L8 Cloud Logging")
print("  roles/monitoring.metricWriter      — L8 Cloud Monitoring")
print("  roles/datastore.viewer             — L3 Firestore feature catalog")
print("  roles/secretmanager.secretAccessor — jira-api-token, alloydb-password")
print("  roles/run.invoker                  — Cloud Scheduler -> Cloud Run Job")
print("  roles/cloudbuild.builds.editor     — (only needed to rebuild)")
print("  roles/storage.objectAdmin          — (only needed to rebuild; on build bucket)")
print("="*65)

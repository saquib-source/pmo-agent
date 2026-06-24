"""Phase 6: Fix ALL_PROJECTS to read from JIRA_PROJECTS env var (ISRDS-only)."""
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

SKIP = {
    ".venv", "__pycache__", ".git",
    "adk/service-account.json", "adk/.env", "adk/trust-ledger.jsonl",
    "adk/logs", "adk/briefs",
    "phase1_apis_secrets.py", "phase2_db.py", "phase3_build_deploy.py",
    "phase4_rebuild.py", "phase5_fix.py", "phase6_rebuild.py",
    "provision_gcp.py", "provision_results.json",
    "check_apis.py", "find_db.py", "authorize_and_migrate.py",
    "check_execution.py", "fetch_logs.py", "poll_exec.py",
}

def excluded(rel_str):
    parts = rel_str.replace("\\", "/").lstrip("./").split("/")
    for p in parts:
        if p in {"__pycache__", ".venv", ".git"}: return True
    normed = rel_str.replace("\\", "/").lstrip("./")
    for s in SKIP:
        if normed == s.lstrip("./") or normed.startswith(s.rstrip("/") + "/"): return True
    return False

print("[1] Archiving...")
archive = Path(tempfile.gettempdir()) / "pmo-swarm-v4.tar.gz"
n = 0
with tarfile.open(archive, "w:gz") as tar:
    for item in Path(".").rglob("*"):
        rel = str(item.relative_to("."))
        if not excluded(rel) and item.is_file():
            tar.add(item, arcname=rel); n += 1
print(f"  {n} files, {archive.stat().st_size//1024} KB")

print("\n[2] Uploading...")
gcs = storage.Client(project=PROJECT, credentials=creds)
gcs.bucket(BUCKET).blob("pmo-swarm-v4.tar.gz").upload_from_filename(str(archive))
print(f"  Uploaded")

print("\n[3] Cloud Build...")
cb = discovery.build("cloudbuild", "v1", credentials=creds, cache_discovery=False)
resp = cb.projects().builds().create(projectId=PROJECT, body={
    "source": {"storageSource": {"bucket": BUCKET, "object": "pmo-swarm-v4.tar.gz"}},
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

# No changes needed to job config — JIRA_PROJECTS=ISRDS already set in Cloud Run env
print("\n[4] Triggering test execution...")
run_svc = discovery.build("run", "v2", credentials=creds, cache_discovery=False)
job_path = f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB_NAME}"
er = run_svc.projects().locations().jobs().run(name=job_path, body={}).execute()
eid = er.get("metadata", {}).get("name", "").split("/")[-1]
print(f"  Execution: {eid}")
exec_path = f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB_NAME}/executions/{eid}"
print("  Polling (up to 8 min)...")
for i in range(48):
    time.sleep(10)
    ei = run_svc.projects().locations().jobs().executions().get(name=exec_path).execute()
    succ, fail, run_ = ei.get("succeededCount",0), ei.get("failedCount",0), ei.get("runningCount",0)
    comp = ei.get("completionTime","")
    if comp or succ > 0:
        print(f"\n  SUCCEEDED ({i*10}s)")
        break
    if fail > 0 and run_ == 0:
        print(f"\n  FAILED")
        break
    if i % 3 == 0:
        print(f"  [{i*10}s] succeeded={succ} failed={fail} running={run_}")
else:
    print(f"\n  Still running after 8 min — execution: {eid}")
    print(f"  Check: https://console.cloud.google.com/run/jobs/{JOB_NAME}/executions?project={PROJECT}")

print("\n" + "="*55)
print(f"REBUILD DONE")
print(f"  ALL_PROJECTS now reads from JIRA_PROJECTS env var")
print(f"  With JIRA_PROJECTS=ISRDS: only ISRDS scanned")
print(f"  Execution: {eid}")
print("="*55)

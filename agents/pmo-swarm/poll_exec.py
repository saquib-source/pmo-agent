"""Poll current execution and show latest logs."""
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from google.oauth2 import service_account
from googleapiclient import discovery

PROJECT  = "isr-division-systems-488723"
REGION   = "us-central1"
JOB_NAME = "pmo-swarm"
EXEC_ID  = "pmo-swarm-468dc"

creds = service_account.Credentials.from_service_account_file(
    "adk/service-account.json", scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
run_svc = discovery.build("run", "v2", credentials=creds, cache_discovery=False)
log_svc = discovery.build("logging", "v2", credentials=creds, cache_discovery=False)

exec_path = f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB_NAME}/executions/{EXEC_ID}"

print(f"=== Execution status: {EXEC_ID} ===")
for i in range(40):
    ei = run_svc.projects().locations().jobs().executions().get(name=exec_path).execute()
    succ, fail, run_ = ei.get("succeededCount",0), ei.get("failedCount",0), ei.get("runningCount",0)
    comp = ei.get("completionTime","")
    print(f"  [{i*15}s] succeeded={succ} failed={fail} running={run_}  comp={comp[:19]}")
    if comp or succ > 0:
        print("SUCCEEDED")
        break
    if fail > 0 and run_ == 0:
        print("FAILED")
        break
    if i == 39:
        print("Timed out polling")
        break
    time.sleep(15)

print("\n=== Latest logs (most recent 30 entries) ===")
resp = log_svc.entries().list(body={
    "resourceNames": [f"projects/{PROJECT}"],
    "filter": 'resource.type="cloud_run_job" resource.labels.job_name="pmo-swarm"',
    "orderBy": "timestamp desc",
    "pageSize": 30,
}).execute()
for entry in reversed(resp.get("entries", [])):
    ts = entry.get("timestamp", "")[:23]
    sev = entry.get("severity", "DFLT")[:5]
    payload = entry.get("textPayload") or str(entry.get("jsonPayload", {}).get("message", entry.get("jsonPayload", {}).get("detail", entry.get("jsonPayload", ""))))
    if not str(payload).strip(): continue
    mk = "***" if sev in ("ERROR","CRITI") else ("WRN" if sev=="WARNI" else "   ")
    print(f"{mk} {ts} [{sev}] {str(payload)[:250]}")

"""Check Cloud Run Job execution status and tail logs."""
import io, sys, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from google.oauth2 import service_account
from googleapiclient import discovery

PROJECT  = "isr-division-systems-488723"
REGION   = "us-central1"
JOB_NAME = "pmo-swarm"
EXEC_NAME = "pmo-swarm-4qbth"

creds = service_account.Credentials.from_service_account_file(
    "adk/service-account.json",
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
run_svc = discovery.build("run", "v2", credentials=creds, cache_discovery=False)

exec_path = f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB_NAME}/executions/{EXEC_NAME}"

print(f"Polling execution: {EXEC_NAME}")
for i in range(60):
    exec_info = run_svc.projects().locations().jobs().executions().get(
        name=exec_path
    ).execute()
    conditions = exec_info.get("conditions", [])
    status_msg = "; ".join(f"{c.get('type')}={c.get('status')} {c.get('message','')}" for c in conditions)
    succeeded = exec_info.get("succeededCount", 0)
    failed = exec_info.get("failedCount", 0)
    running = exec_info.get("runningCount", 0)
    comp_time = exec_info.get("completionTime", "")

    print(f"  [{i*10}s] succeeded={succeeded} failed={failed} running={running}  {status_msg[:120]}")

    if comp_time or succeeded > 0:
        print(f"\nExecution COMPLETE at {comp_time}")
        print(f"  Succeeded: {succeeded}  Failed: {failed}")
        if failed > 0:
            print("  Check logs: https://console.cloud.google.com/logs/query?project=" + PROJECT)
        break
    if failed > 0 and running == 0:
        print(f"\nExecution FAILED")
        print(f"  Check logs: https://console.cloud.google.com/logs/query?project={PROJECT}")
        break
    time.sleep(10)

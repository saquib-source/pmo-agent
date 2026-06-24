import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from google.oauth2 import service_account
from googleapiclient import discovery

creds = service_account.Credentials.from_service_account_file(
    'adk/service-account.json',
    scopes=['https://www.googleapis.com/auth/cloud-platform']
)
svc = discovery.build('serviceusage', 'v1', credentials=creds, cache_discovery=False)
resp = svc.services().list(
    parent='projects/isr-division-systems-488723',
    filter='state:ENABLED',
    pageSize=200
).execute()
enabled = sorted(s['name'].split('/')[-1] for s in resp.get('services', []))

targets = [
    'alloydb.googleapis.com',
    'aiplatform.googleapis.com',
    'bigquery.googleapis.com',
    'logging.googleapis.com',
    'monitoring.googleapis.com',
    'run.googleapis.com',
    'cloudbuild.googleapis.com',
    'secretmanager.googleapis.com',
    'cloudscheduler.googleapis.com',
    'cloudresourcemanager.googleapis.com',
]

print("Total enabled APIs: " + str(len(enabled)))
print("")
for t in targets:
    status = "ENABLED" if t in enabled else "MISSING"
    print("  " + status + "  " + t)

"""
GCP Provisioning Script - PMO Swarm
Uses swarm-558 service account to provision all required GCP resources.
Run from pmo-swarm/ directory.
"""
import json
import os
import sys
import time
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

SA_KEY = "adk/service-account.json"
PROJECT = "isr-division-systems-488723"
REGION  = "us-central1"
SA_EMAIL = "swarm-558@isr-division-systems-488723.iam.gserviceaccount.com"

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
]

IAM_ROLES = [
    "roles/aiplatform.user",
    "roles/aiplatform.reasoningEngineUser",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/datastore.viewer",
    "roles/alloydb.client",
    "roles/run.invoker",
    "roles/secretmanager.secretAccessor",
]

results = {"passed": [], "failed": [], "warnings": []}

def ok(msg):
    results["passed"].append(msg)
    print(f"  ✓  {msg}")

def fail(msg):
    results["failed"].append(msg)
    print(f"  ✗  {msg}")

def warn(msg):
    results["warnings"].append(msg)
    print(f"  ⚠  {msg}")

# ── Auth ──────────────────────────────────────────────────────────────────────
print("\n[1/5] Authentication")
try:
    from google.oauth2 import service_account
    from googleapiclient import discovery

    creds = service_account.Credentials.from_service_account_file(
        SA_KEY,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    ok(f"Service account loaded: {SA_EMAIL}")
except Exception as e:
    fail(f"Auth failed: {e}")
    sys.exit(1)

# ── Enable APIs ───────────────────────────────────────────────────────────────
print("\n[2/5] Enable GCP APIs")
try:
    svc = discovery.build("serviceusage", "v1", credentials=creds, cache_discovery=False)
    enabled = []
    for api in APIS:
        name = f"projects/{PROJECT}/services/{api}"
        try:
            resp = svc.services().enable(name=name).execute()
            op_name = resp.get("name", "")
            if "operations" in op_name:
                # Poll until done
                for _ in range(30):
                    op = svc.operations().get(name=op_name).execute()
                    if op.get("done"):
                        break
                    time.sleep(2)
            ok(f"Enabled: {api}")
            enabled.append(api)
        except Exception as e:
            err = str(e)
            if "already enabled" in err.lower() or "403" not in err:
                ok(f"Already enabled: {api}")
                enabled.append(api)
            else:
                fail(f"Cannot enable {api}: {err[:120]}")
except Exception as e:
    fail(f"Service Usage API client failed: {e}")

# ── IAM Roles ─────────────────────────────────────────────────────────────────
print("\n[3/5] Grant IAM roles to swarm-558")
try:
    rm = discovery.build("cloudresourcemanager", "v1", credentials=creds, cache_discovery=False)
    policy = rm.projects().getIamPolicy(resource=PROJECT, body={}).execute()
    bindings = policy.get("bindings", [])
    member = f"serviceAccount:{SA_EMAIL}"
    changed = False
    for role in IAM_ROLES:
        existing = next((b for b in bindings if b["role"] == role), None)
        if existing:
            if member not in existing.get("members", []):
                existing["members"].append(member)
                changed = True
                ok(f"Added to existing binding: {role}")
            else:
                ok(f"Already has role: {role}")
        else:
            bindings.append({"role": role, "members": [member]})
            changed = True
            ok(f"New binding created: {role}")

    if changed:
        policy["bindings"] = bindings
        rm.projects().setIamPolicy(
            resource=PROJECT,
            body={"policy": policy}
        ).execute()
        ok("IAM policy updated on project")
    else:
        ok("All roles already present — no update needed")
except Exception as e:
    fail(f"IAM update failed: {e}")
    warn("Roles must be granted manually in GCP Console → IAM → swarm-558")

# ── AlloyDB ───────────────────────────────────────────────────────────────────
print("\n[4/5] Create AlloyDB cluster + primary instance")
try:
    from google.cloud import alloydb_v1
    from google.api_core.exceptions import AlreadyExists, FailedPrecondition

    adb = alloydb_v1.AlloyDBAdminClient(credentials=creds)
    parent = f"projects/{PROJECT}/locations/{REGION}"

    # Cluster
    cluster_id = "isrds-agentic"
    cluster_name = f"{parent}/clusters/{cluster_id}"
    try:
        cluster = alloydb_v1.Cluster()
        cluster.network = f"projects/{PROJECT}/global/networks/default"
        cluster.initial_user = alloydb_v1.UserPassword(
            user="postgres",
            password=os.environ.get("ALLOYDB_PASSWORD", "")
        )
        op = adb.create_cluster(
            parent=parent,
            cluster_id=cluster_id,
            cluster=cluster,
        )
        print("  ⏳ Creating AlloyDB cluster (takes ~3 min)...")
        result = op.result(timeout=300)
        ok(f"AlloyDB cluster created: {cluster_id}")
    except AlreadyExists:
        ok(f"AlloyDB cluster already exists: {cluster_id}")
    except Exception as e:
        fail(f"AlloyDB cluster creation failed: {str(e)[:200]}")

    # Primary instance
    instance_id = "isrds-primary"
    try:
        instance = alloydb_v1.Instance()
        instance.instance_type = alloydb_v1.Instance.InstanceType.PRIMARY
        instance.machine_config = alloydb_v1.Instance.MachineConfig(cpu_count=2)
        op = adb.create_instance(
            parent=cluster_name,
            instance_id=instance_id,
            instance=instance,
        )
        print("  ⏳ Creating AlloyDB primary instance (takes ~5 min)...")
        result = op.result(timeout=600)
        ok(f"AlloyDB primary instance created: {instance_id}")

        # Get private IP
        inst = adb.get_instance(name=f"{cluster_name}/instances/{instance_id}")
        ip = inst.ip_address
        ok(f"AlloyDB private IP: {ip}")
        results["alloydb_ip"] = ip
    except AlreadyExists:
        ok(f"AlloyDB instance already exists: {instance_id}")
        try:
            inst = adb.get_instance(name=f"{cluster_name}/instances/{instance_id}")
            ip = inst.ip_address
            ok(f"AlloyDB private IP: {ip}")
            results["alloydb_ip"] = ip
        except Exception as e2:
            warn(f"Could not retrieve AlloyDB IP: {e2}")
    except Exception as e:
        fail(f"AlloyDB instance creation failed: {str(e)[:200]}")

except Exception as e:
    fail(f"AlloyDB client error: {e}")

# ── Store secrets in Secret Manager ──────────────────────────────────────────
print("\n[5/5] Store secrets in Secret Manager")
try:
    from google.cloud import secretmanager

    sm = secretmanager.SecretManagerServiceClient(credentials=creds)
    sm_parent = f"projects/{PROJECT}"

    # Read from env — never hardcode credentials.
    #   export JIRA_API_TOKEN=...  ALLOYDB_PASSWORD=...
    secrets = {
        "jira-api-token": os.environ.get("JIRA_API_TOKEN", ""),
        "alloydb-password": os.environ.get("ALLOYDB_PASSWORD", ""),
    }

    for name, value in secrets.items():
        secret_path = f"{sm_parent}/secrets/{name}"
        try:
            sm.create_secret(
                request={
                    "parent": sm_parent,
                    "secret_id": name,
                    "secret": {"replication": {"automatic": {}}},
                }
            )
        except Exception:
            pass  # already exists
        try:
            sm.add_secret_version(
                request={
                    "parent": secret_path,
                    "payload": {"data": value.encode()},
                }
            )
            ok(f"Secret stored: {name}")
        except Exception as e:
            fail(f"Could not store {name}: {e}")

except Exception as e:
    fail(f"Secret Manager failed: {e}")

# ── Summary ───────────────────────────────────────────────────────────────────
print("\n" + "="*60)
print(f"PASSED:   {len(results['passed'])}")
print(f"FAILED:   {len(results['failed'])}")
print(f"WARNINGS: {len(results['warnings'])}")
if results.get("alloydb_ip"):
    print(f"\nAlloyDB IP → set ALLOYDB_HOST={results['alloydb_ip']} in .env")
if results["failed"]:
    print("\nFailed items:")
    for f in results["failed"]:
        print(f"  - {f}")
print("="*60)

with open("provision_results.json", "w") as fh:
    json.dump(results, fh, indent=2)
print("Results saved → provision_results.json")

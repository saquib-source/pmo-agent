"""
Authorize this machine's IP on Cloud SQL, run all 4 migrations, then remove it.
"""
import io, os, sys, time, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from google.oauth2 import service_account
from googleapiclient import discovery
import psycopg2
from psycopg2 import sql as pgsql
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT
from pathlib import Path

PROJECT   = "isr-division-systems-488723"
INSTANCE  = "tier3"
REGION    = "us-central1"
HOST      = "34.16.10.122"
PORT      = 5432
USER      = "postgres"
PASSWORD  = os.environ.get("ALLOYDB_PASSWORD", "")
MAIN_DB   = "isrd_db"
AGENT_DB  = "isrds_agentic"
MIGRATIONS_DIR = Path(r"C:\Manmeet\AG\isrd\migrations")

MIGRATION_FILES = [
    "001_foundation.sql",
    "002_survey.sql",
    "003_seed_config_registry.sql",
    "004_pmo_swarm.sql",
]

# ─── Auth ────────────────────────────────────────────────────────────────────
creds = service_account.Credentials.from_service_account_file(
    "adk/service-account.json",
    scopes=["https://www.googleapis.com/auth/cloud-platform"]
)
sql_svc = discovery.build("sqladmin", "v1", credentials=creds, cache_discovery=False)

# ─── Get my public IP ────────────────────────────────────────────────────────
my_ip = urllib.request.urlopen("https://api.ipify.org", timeout=5).read().decode()
print(f"[AUTH] My public IP: {my_ip}")

# ─── Get current authorized networks ────────────────────────────────────────
print(f"\n[1] Fetching Cloud SQL instance settings for {INSTANCE}...")
inst = sql_svc.instances().get(project=PROJECT, instance=INSTANCE).execute()
ip_config = inst.get("settings", {}).get("ipConfiguration", {})
auth_nets = ip_config.get("authorizedNetworks", [])
print(f"  Current authorized networks: {[n.get('value') for n in auth_nets]}")

CIDR = f"{my_ip}/32"
my_net_name = "claude-code-temp"

already_authorized = any(n.get("value") == CIDR for n in auth_nets)
if already_authorized:
    print(f"  IP {CIDR} already authorized")
else:
    print(f"\n[2] Adding {CIDR} to authorized networks...")
    new_nets = auth_nets + [{"name": my_net_name, "value": CIDR}]
    patch_body = {
        "settings": {
            "ipConfiguration": {
                "authorizedNetworks": new_nets,
                "ipv4Enabled": ip_config.get("ipv4Enabled", True),
            }
        }
    }
    op = sql_svc.instances().patch(project=PROJECT, instance=INSTANCE, body=patch_body).execute()
    op_name = op.get("name", "")
    print(f"  Waiting for Cloud SQL patch to apply...")
    for _ in range(60):
        result = sql_svc.operations().get(project=PROJECT, operation=op_name).execute()
        status = result.get("status")
        if status == "DONE":
            err = result.get("error")
            if err:
                print(f"  ERROR: {err}")
                sys.exit(1)
            print(f"  Authorized {CIDR} on {INSTANCE}")
            break
        time.sleep(5)
    else:
        print("  Timed out waiting for authorization. Proceeding anyway...")
    time.sleep(5)  # brief settle

# ─── Create isrds_agentic database ──────────────────────────────────────────
print(f"\n[3] Connect to {HOST}:{PORT}/{MAIN_DB} and create {AGENT_DB}...")
try:
    conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                            dbname=MAIN_DB, connect_timeout=15)
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (AGENT_DB,))
    if cur.fetchone():
        print(f"  Database '{AGENT_DB}' already exists")
    else:
        cur.execute(pgsql.SQL("CREATE DATABASE {}").format(pgsql.Identifier(AGENT_DB)))
        print(f"  Created database: {AGENT_DB}")
    cur.close()
    conn.close()
except Exception as e:
    print(f"  ERROR: {e}")
    print("  Will remove temp authorization and exit.")
    _cleanup(sql_svc, PROJECT, INSTANCE, auth_nets, ip_config)
    sys.exit(1)

# ─── Run migrations ──────────────────────────────────────────────────────────
print(f"\n[4] Running migrations against {AGENT_DB}...")
for fname in MIGRATION_FILES:
    fpath = MIGRATIONS_DIR / fname
    if not fpath.exists():
        print(f"  MISSING: {fpath}")
        continue
    sql_text = fpath.read_text(encoding="utf-8")
    try:
        conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                                dbname=AGENT_DB, connect_timeout=15)
        conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
        cur = conn.cursor()
        cur.execute(sql_text)
        cur.close()
        conn.close()
        print(f"  OK  {fname}")
    except Exception as e:
        msg = str(e)
        if "already exists" in msg.lower():
            print(f"  OK  {fname} (already applied)")
        else:
            print(f"  ERR {fname}: {msg[:300]}")

# ─── Verify tables ────────────────────────────────────────────────────────────
print(f"\n[5] Verifying tables in {AGENT_DB}...")
try:
    conn = psycopg2.connect(host=HOST, port=PORT, user=USER, password=PASSWORD,
                            dbname=AGENT_DB, connect_timeout=15)
    cur = conn.cursor()
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' ORDER BY table_name")
    tables = [r[0] for r in cur.fetchall()]
    for t in tables:
        print(f"  table: {t}")
    print(f"  Total: {len(tables)} tables")
    cur.execute("SELECT role_category, engine_binding FROM config_registry ORDER BY role_category")
    rows = cur.fetchall()
    print(f"\n  config_registry rows: {len(rows)}")
    for r in rows:
        print(f"    {r[0]}  |  {r[1]}")
    cur.close()
    conn.close()
except Exception as e:
    print(f"  ERROR verifying: {e}")

# ─── Remove temp authorization ────────────────────────────────────────────────
if not already_authorized:
    print(f"\n[6] Removing temp authorization {CIDR}...")
    clean_nets = [n for n in auth_nets if n.get("value") != CIDR]
    patch_body = {
        "settings": {
            "ipConfiguration": {
                "authorizedNetworks": clean_nets,
                "ipv4Enabled": ip_config.get("ipv4Enabled", True),
            }
        }
    }
    op = sql_svc.instances().patch(project=PROJECT, instance=INSTANCE, body=patch_body).execute()
    op_name = op.get("name", "")
    for _ in range(60):
        result = sql_svc.operations().get(project=PROJECT, operation=op_name).execute()
        if result.get("status") == "DONE":
            print(f"  Removed {CIDR} from authorized networks")
            break
        time.sleep(5)
    else:
        print("  WARNING: Timed out removing auth. Remove manually: GCP Console > Cloud SQL > tier3 > Connections")

print("\n[DONE] Database setup complete.")

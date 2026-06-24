"""Find the postgres instance and check firewall access."""
import io, sys
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from google.oauth2 import service_account
from googleapiclient import discovery

creds = service_account.Credentials.from_service_account_file(
    'adk/service-account.json',
    scopes=['https://www.googleapis.com/auth/cloud-platform']
)
PROJECT = "isr-division-systems-488723"
TARGET_IP = "34.16.10.122"

# Check Cloud SQL instances
print("[1] Cloud SQL instances...")
try:
    sql_svc = discovery.build('sqladmin', 'v1', credentials=creds, cache_discovery=False)
    resp = sql_svc.instances().list(project=PROJECT).execute()
    instances = resp.get('items', [])
    for inst in instances:
        name = inst.get('name')
        state = inst.get('state')
        db_ver = inst.get('databaseVersion')
        ips = [ip.get('ipAddress') for ip in inst.get('ipAddresses', [])]
        print(f"  instance: {name}  state: {state}  version: {db_ver}")
        print(f"  IPs: {ips}")
        if TARGET_IP in ips:
            print(f"  *** MATCH: {name} has IP {TARGET_IP}")
except Exception as e:
    print(f"  ERROR: {e}")

# Check Compute Engine instances with that external IP
print("\n[2] Compute Engine instances with that IP...")
try:
    compute = discovery.build('compute', 'v1', credentials=creds, cache_discovery=False)
    resp = compute.instances().aggregatedList(project=PROJECT).execute()
    for zone, data in resp.get('items', {}).items():
        for inst in data.get('instances', []):
            for iface in inst.get('networkInterfaces', []):
                for ac in iface.get('accessConfigs', []):
                    if ac.get('natIP') == TARGET_IP:
                        print(f"  *** GCE MATCH: {inst['name']} zone={zone} IP={TARGET_IP}")
except Exception as e:
    print(f"  ERROR: {e}")

# Get my current public IP
print("\n[3] Getting this machine's public IP for firewall auth...")
import urllib.request
try:
    my_ip = urllib.request.urlopen('https://api.ipify.org', timeout=5).read().decode()
    print(f"  This machine's public IP: {my_ip}")
except Exception as e:
    print(f"  Could not fetch public IP: {e}")

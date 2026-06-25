"""Detect the Jira Sprint custom-field id for this instance.

Reads JIRA_* from adk/.env, queries /rest/api/3/field, and prints the field id
backing 'Sprint' (schema custom == com.pyxis.greenhopper.jira:gh-sprint).
Set JIRA_SPRINT_FIELD to the printed id if it is not the default customfield_10020.
"""
import os
import sys
from pathlib import Path

import httpx

ENV = Path(__file__).parent / "adk" / ".env"
for line in ENV.read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())

url   = os.environ["JIRA_URL"].rstrip("/")
email = os.environ["JIRA_EMAIL"]
token = os.environ["JIRA_API_TOKEN"]
verify = os.environ.get("SSL_CERT_FILE") or True

r = httpx.get(f"{url}/rest/api/3/field", auth=(email, token),
              headers={"Accept": "application/json"}, timeout=30.0,
              verify=verify, follow_redirects=True)
r.raise_for_status()

sprint_fields = []
for f in r.json():
    schema = f.get("schema") or {}
    if schema.get("custom") == "com.pyxis.greenhopper.jira:gh-sprint" or f.get("name") == "Sprint":
        sprint_fields.append((f.get("id"), f.get("name"), schema.get("custom")))

if not sprint_fields:
    print("No Sprint field found. This instance may not use sprints.", file=sys.stderr)
    sys.exit(2)

for fid, name, custom in sprint_fields:
    print(f"{fid}\t{name}\t{custom}")

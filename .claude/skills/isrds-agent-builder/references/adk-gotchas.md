# ADK Agent Builder — Gotchas, Fixes & Lessons Learned

> **When to read this:** Before building ANY new agent with Google ADK + Jira + Vertex AI.
> Every issue below was hit in production and cost hours to debug. Read it all.

---

## 1. Corporate Proxy / SSL Certificate Errors (LOCAL DEV ONLY)

> ⚠️ **This section applies ONLY to local development behind a corporate firewall.**
> In production (Cloud Run, Compute Engine, any GCP service), SSL works natively.
> Do NOT deploy custom PEM bundles or SSL env vars to production.

### Problem (local dev only)
Behind a corporate firewall/proxy, outbound HTTPS calls fail locally with:
```
ssl.SSLCertVerificationError: [SSL: CERTIFICATE_VERIFY_FAILED]
```
This affects local testing of: Jira API, Vertex AI (Gemini), pip install, gcloud.

### Fix (local dev only)
Create a combined PEM bundle and set THREE environment variables **in your local shell**:

```bash
# 1. Export corporate root CA (from macOS Keychain)
security find-certificate -a -p /System/Library/Keychains/SystemRootCertificates.keychain > system-certs.pem
security find-certificate -a -p ~/Library/Keychains/login.keychain-db >> system-certs.pem

# 2. Append Python's default certs
python3 -c "import certifi; print(open(certifi.where()).read())" >> combined-ca-certs.pem
cat system-certs.pem >> combined-ca-certs.pem

# 3. Set ALL THREE (missing any one = failure)
export SSL_CERT_FILE=/path/to/combined-ca-certs.pem
export REQUESTS_CA_BUNDLE=/path/to/combined-ca-certs.pem
export GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/path/to/combined-ca-certs.pem
```

### Local .env (do NOT deploy these to production)
```
# LOCAL DEV ONLY — remove these lines before deploying
SSL_CERT_FILE=/absolute/path/to/combined-ca-certs.pem
REQUESTS_CA_BUNDLE=/absolute/path/to/combined-ca-certs.pem
GRPC_DEFAULT_SSL_ROOTS_FILE_PATH=/absolute/path/to/combined-ca-certs.pem
```

### In Python code — use conditional SSL verify
```python
import httpx, os

# Local dev: uses custom cert bundle; Production: uses system default (True)
ssl_cert = os.environ.get("SSL_CERT_FILE")
verify = ssl_cert if ssl_cert else True  # True = system default in production

httpx.Client(verify=verify)
```

### pip install behind proxy (local only)
```bash
pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org <package>
# OR use truststore:
pip install truststore
python -c "import truststore; truststore.inject_into_ssl()"
```

### Production — no SSL workarounds needed
In GCP (Cloud Run, Compute Engine, Cloud Functions), SSL certificates are managed
by the platform. Do NOT:
- Ship `combined-ca-certs.pem` in your Docker image
- Set `SSL_CERT_FILE` in production environment variables
- Import `truststore` in production code

---

## 2. Atlassian Jira REST API v3

### GET /search is DEPRECATED → Use POST /search/jql
```
GET /rest/api/3/search → 410 Gone (deprecated)
POST /rest/api/3/search/jql → ✅ Use this
```

### POST /search/jql does NOT return `total`
The new endpoint uses cursor pagination (`nextPageToken` + `isLast`), NOT offset pagination.

```python
# WRONG — total will be None
data = jira_request("POST", "/search/jql", json_body={...})
total = data.get("total")  # ← Always None!

# RIGHT — count issues directly
issues = data.get("issues", [])
total = len(issues)
is_last = data.get("isLast", True)
next_token = data.get("nextPageToken")
```

### Jira domain redirects → Enable follow_redirects
Atlassian domains often 301-redirect. Without this, you get empty responses:
```python
httpx.Client(follow_redirects=True)  # ← REQUIRED
```

### Jira comments use ADF (Atlassian Document Format), not plain text
```python
# WRONG — plain text (shows as plain text, no formatting)
body = {"body": "Hello team"}

# RIGHT — ADF format
body = {"body": {"version": 1, "type": "doc", "content": [
    {"type": "paragraph", "content": [{"type": "text", "text": "Hello team"}]}
]}}
```

### Jira @mentions require accountId, not display name
```python
# WRONG — shows as plain text "Hi Todd B."
{"type": "text", "text": "Hi Todd B."}

# RIGHT — clickable @mention that sends notification
{"type": "mention", "attrs": {
    "id": "5a541d3e409b5e1fb4487ecf",  # accountId from /user/assignable/search
    "text": "@Todd B.",
    "accessLevel": ""
}}
```

### How to get accountId for @mentions
```python
# Search assignable users for a project
resp = jira_request("GET", "/user/assignable/search", params={
    "project": "ISRDS", "maxResults": 100
})
# Each user has: displayName, accountId, emailAddress
```

### Don't fetch `comment` field in bulk JQL queries
Including "comment" in the fields list makes each request 10x slower because
it loads ALL comments for ALL issues. Only fetch comments on individual issues
via `GET /issue/{key}`.

```python
# FAST — bulk scan
fields = ["summary", "status", "assignee", "priority", "updated", "issuelinks"]

# SLOW — avoid in bulk
fields = ["summary", "status", "assignee", "priority", "updated", "comment"]  # ← comment is heavy!

# For comments, fetch per-issue:
jira_request("GET", f"/issue/{key}", params={"fields": "comment"})
```

---

## 3. Google ADK (Agent Development Kit) 2.x

### ADK 2.x FunctionTool requires "thin" wrappers
ADK 2.x inspects function signatures. Internal helper functions must be wrapped in
thin public functions that ADK can introspect:

```python
# WRONG — ADK can't handle complex internal functions
tools = [FunctionTool(_complex_internal_function)]

# RIGHT — thin wrapper with simple params
def run_jql(jql: str, max_results: int = 50) -> dict:
    """Run a JQL query against Jira."""
    return _run_jql(jql, max_results)

tools = [FunctionTool(run_jql)]
```

### __init__.py must export `root_agent`
ADK expects the agent entry point at the module level:
```python
# adk/__init__.py
from .agent import root_agent
```

### .env is auto-loaded by ADK when running `adk web .`
- Place `.env` in the same directory as `__init__.py`
- ADK loads it automatically — you don't need `dotenv` in agent.py for the chat mode
- BUT: for standalone scripts (daemon), you DO need `load_dotenv()` explicitly

### Model resolution
```python
# In .env — this sets the runtime model
AGENT_MODEL=gemini-2.5-flash

# In agent.py — read dynamically
MODEL = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")
agent = LlmAgent(model=MODEL, ...)
```

---

## 4. Standalone Daemon vs ADK Chat Agent

### Problem: Importing from agent.py in the daemon causes hangs
The ADK agent module imports `google.adk`, `google.genai`, etc. These are heavy
and can hang during import (especially with auth token refresh).

### Fix: Make the daemon 100% standalone
```python
# pmo_daemon.py — NEVER import from agent.py
# Instead, duplicate the Jira functions using plain httpx
import httpx  # lightweight, no ADK dependency

def _jira(method, path, **kw):
    """Direct Jira API — no ADK."""
    with httpx.Client(auth=(EMAIL, TOKEN), ...) as c:
        return c.request(method, f"{URL}/rest/api/3{path}", **kw)
```

### For AI in the daemon, call Vertex AI REST API directly
Don't import google.genai or google.adk. Use httpx + google.auth:
```python
import google.auth
import google.auth.transport.requests

creds, _ = google.auth.default()
creds.refresh(google.auth.transport.requests.Request())

httpx.post(
    f"https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/..."
    headers={"Authorization": f"Bearer {creds.token}"},
    json={"contents": [{"role": "user", "parts": [{"text": prompt}]}]},
)
```

### Module-level env vars get cached at import time
If you read `os.environ.get("JIRA_URL")` at module level, and the daemon loads .env
AFTER importing the module, the values are empty strings:

```python
# WRONG — cached at import time
JIRA_URL = os.environ.get("JIRA_URL", "")  # Empty if .env loaded later!

# RIGHT — read dynamically in functions
def _jira_config():
    return (os.environ.get("JIRA_URL"), os.environ.get("JIRA_EMAIL"), ...)
```

---

## 5. Python Logging in Background Daemons

### Problem: Log file appears empty even though daemon is running
`logging.basicConfig()` can be overridden by other libraries. File handlers
buffer output and don't flush immediately.

### Fix: Create explicit handlers + use PYTHONUNBUFFERED
```python
log = logging.getLogger("pmo")
log.setLevel(logging.INFO)
log.handlers.clear()  # ← Clear any inherited handlers

fh = logging.FileHandler("logs/daemon.log")
fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(fh)

ch = logging.StreamHandler()
ch.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
log.addHandler(ch)
```

Run with:
```bash
PYTHONUNBUFFERED=1 python pmo_daemon.py --once
```

### Never use shell redirect `>` to clear log files before running
```bash
# WRONG — creates race condition with Python's FileHandler
> logs/daemon.log && python daemon.py

# RIGHT — delete and let Python recreate
rm -f logs/daemon.log && python daemon.py
```

---

## 6. GCP / Vertex AI Authentication

### Service account key vs Application Default Credentials
```bash
# Option A: Service account key (for local dev)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json

# Option B: ADC (for cloud deployment — preferred)
gcloud auth application-default login
```

### Required .env variables for Vertex AI
```
GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account-key.json
GOOGLE_CLOUD_PROJECT=your-project-id
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_GENAI_USE_VERTEXAI=TRUE
```

### ADK expects GOOGLE_GENAI_USE_VERTEXAI=TRUE
Without this, ADK tries to use Google AI Studio instead of Vertex AI and fails
with auth errors.

---

## 7. Jira Authentication

### API Token (not OAuth, not password)
1. Go to: https://id.atlassian.com/manage-profile/security/api-tokens
2. Create token
3. Use as password in Basic Auth:
```python
httpx.Client(auth=(JIRA_EMAIL, JIRA_API_TOKEN))
```

### Finding your Jira URL and Project Key
- **JIRA_URL**: Your Atlassian domain, e.g., `https://yourcompany.atlassian.net`
- **JIRA_PROJECT**: The project key (e.g., `ISRDS`) — found in the URL when viewing a project

### 401 errors after token creation
- Token must be created by the SAME email used in `JIRA_EMAIL`
- The email must have project access (check project permissions in Jira admin)
- If behind corporate proxy, ensure `follow_redirects=True`

---

## 8. Agent Architecture — What We Learned

### Two-mode architecture is the right pattern
```
├── adk/agent.py        # Chat mode (ADK web UI) — interactive PMO
├── adk/pmo_daemon.py   # Daemon mode (standalone) — autonomous scanning
```

- **Chat agent** uses full ADK framework (LlmAgent, FunctionTool, etc.)
- **Daemon** is standalone Python (httpx + google.auth only — no ADK imports)
- Both read from the same `.env` and write to the same trust ledger

### Daemon should use `--once` mode for cloud deployment
```bash
python pmo_daemon.py              # Continuous loop (for VM/systemd)
python pmo_daemon.py --once       # Single cycle (for Cloud Run Jobs)
python pmo_daemon.py --brief      # Brief only (for testing)
```

### AI responses need context (description + comments)
Template-based comments are robotic. The agent should:
1. Read the ticket description
2. Read the last 5 comments
3. Pass context to Gemini to generate a human-like response
4. Post with proper @mention

### Trust Ledger is non-negotiable
Every action (comment, escalation, decision) gets logged to `trust-ledger.jsonl`:
```python
{"timestamp": "...", "type": "auto-chase", "detail": "Posted on ISRDS-1510 → @Todd B."}
```

---

## 9. Quick Start Checklist for New Agent

```
1. [ ] Create folder: agents/<agent-name>/adk/
2. [ ] Create .venv: python3 -m venv .venv && source .venv/bin/activate
3. [ ] Install deps: pip install google-adk httpx python-dotenv truststore
4. [ ] Create combined-ca-certs.pem (Section 1 above)
5. [ ] Create .env with ALL required vars (SSL, GCP, Jira, model)
6. [ ] Create __init__.py exporting root_agent
7. [ ] Create agent.py with LlmAgent + FunctionTool wrappers
8. [ ] Test: adk web . → http://localhost:8000
9. [ ] Create pmo_daemon.py (standalone, no ADK imports)
10. [ ] Test: PYTHONUNBUFFERED=1 python pmo_daemon.py --once
11. [ ] Verify: check logs/, briefs/, trust-ledger.jsonl
12. [ ] Deploy: Cloud Run Jobs + Cloud Scheduler (recommended)
```

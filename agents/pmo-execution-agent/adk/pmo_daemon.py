"""
ISRDS PMO Daemon — Autonomous AI-Powered Execution Loop

Standalone daemon (no ADK dependency). Connects directly to Jira REST API
and uses Gemini AI to generate context-aware PMO responses.

Each cycle:
  1. Scans the board for all active tickets
  2. Identifies stalled tickets (configurable thresholds)
  3. For each stalled ticket: reads description + last comments
  4. Uses Gemini to generate a context-aware chase comment
  5. Posts the comment with a real @mention on the assignee
  6. Generates an Operating Brief

Usage:
    python pmo_daemon.py              # Run the daemon (continuous loop)
    python pmo_daemon.py --once       # Run one cycle and exit
    python pmo_daemon.py --brief      # Generate Operating Brief only
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

# ── Load environment ──
load_dotenv(Path(__file__).parent / ".env", override=True)

# ── Resolve relative paths in .env to absolute (portable across machines) ──
_ADK_DIR = Path(__file__).parent
for _var in ["GOOGLE_APPLICATION_CREDENTIALS", "SSL_CERT_FILE",
             "REQUESTS_CA_BUNDLE", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"]:
    _val = os.environ.get(_var)
    if _val:
        _resolved = str((_ADK_DIR / _val).resolve()) if not os.path.isabs(_val) else _val
        if os.path.exists(_resolved):
            os.environ[_var] = _resolved
        else:
            # File missing (e.g., fresh clone without SSL certs) — unset so code uses system defaults
            print(f"⚠️  {_var} points to missing file: {_resolved} — using system defaults")
            os.environ.pop(_var, None)

# ── Logging ──
LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

log = logging.getLogger("pmo")
log.setLevel(logging.INFO)
log.handlers.clear()
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
_fh = logging.FileHandler(LOG_DIR / "pmo_daemon.log")
_fh.setFormatter(_fmt)
log.addHandler(_fh)
_ch = logging.StreamHandler()
_ch.setFormatter(_fmt)
log.addHandler(_ch)


# ══════════════════════════════════════════════════════════════
# Configuration
# ══════════════════════════════════════════════════════════════

JIRA_URL          = os.environ.get("JIRA_URL", "")
JIRA_EMAIL        = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN    = os.environ.get("JIRA_API_TOKEN", "")
JIRA_PROJECT      = os.environ.get("JIRA_PROJECT", "ISRDS")

SCAN_INTERVAL     = int(os.environ.get("PMO_SCAN_INTERVAL_MINUTES", "60"))
STALE_HOURS       = int(os.environ.get("PMO_STALE_THRESHOLD_HOURS", "24"))
CHASE_HOURS       = int(os.environ.get("PMO_CHASE_THRESHOLD_HOURS", "48"))
ESCALATE_HOURS    = int(os.environ.get("PMO_ESCALATE_THRESHOLD_HOURS", "72"))
BRIEF_HOUR        = int(os.environ.get("PMO_BRIEF_HOUR", "7"))
AUTO_COMMENT      = os.environ.get("PMO_AUTO_COMMENT", "false").lower() == "true"

GCP_PROJECT       = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
GCP_LOCATION      = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
AI_MODEL          = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")

BRIEF_DIR = Path(__file__).parent / "briefs"
BRIEF_DIR.mkdir(exist_ok=True)
LEDGER_PATH = Path(__file__).parent / os.environ.get("TRUST_LEDGER_PATH", "trust-ledger.jsonl")

# Local dev only: inject corporate SSL certs if SSL_CERT_FILE is set
# In production (Cloud Run, GCE), this is not needed — system certs work natively
if os.environ.get("SSL_CERT_FILE"):
    try:
        import truststore
        truststore.inject_into_ssl()
    except ImportError:
        pass

# SSL verify: use custom cert bundle locally, system default in production
_SSL_VERIFY = os.environ.get("SSL_CERT_FILE") or True


# ══════════════════════════════════════════════════════════════
# Jira API
# ══════════════════════════════════════════════════════════════

def _jira(method, path, params=None, json_body=None):
    """Authenticated Jira REST API call."""
    import httpx
    try:
        with httpx.Client(
            auth=(JIRA_EMAIL, JIRA_API_TOKEN),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
            verify=_SSL_VERIFY,
            follow_redirects=True,
        ) as c:
            r = c.request(method, f"{JIRA_URL}/rest/api/3{path}", params=params, json=json_body)
            if r.status_code == 204:
                return {"success": True}
            if r.status_code >= 400:
                return {"error": f"Jira {r.status_code}: {r.text[:300]}"}
            return r.json()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _parse_adf_text(adf_body):
    """Extract plain text from an ADF document body."""
    if not adf_body:
        return ""
    parts = []
    for block in adf_body.get("content", []):
        for item in block.get("content", []):
            if item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif item.get("type") == "mention":
                parts.append(item.get("attrs", {}).get("text", ""))
    return " ".join(parts).strip()


def _hours_since(iso_str):
    """Return hours since an ISO timestamp."""
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - dt).total_seconds() / 3600)
    except (ValueError, TypeError):
        return 0


def run_jql(jql, max_results=50):
    """Run JQL and return parsed issues (lightweight — no comments)."""
    data = _jira("POST", "/search/jql", json_body={
        "jql": jql,
        "maxResults": max_results,
        "fields": ["summary", "status", "assignee", "priority",
                   "created", "updated", "issuelinks", "issuetype"],
    })
    if "error" in data:
        return data

    issues = []
    for raw in data.get("issues", []):
        f = raw.get("fields", {})
        a, s, p = f.get("assignee"), f.get("status"), f.get("priority")
        hours = _hours_since(f.get("updated"))
        blockers = []
        for link in f.get("issuelinks", []):
            if link.get("type", {}).get("name") == "Blocks":
                blocked = link.get("inwardIssue") or link.get("outwardIssue")
                if blocked:
                    blockers.append(blocked["key"])
        issues.append({
            "key":             raw.get("key", ""),
            "summary":         f.get("summary", ""),
            "status":          s.get("name", "") if s else "",
            "status_category": s.get("statusCategory", {}).get("name", "") if s else "",
            "assignee":        a.get("displayName", "") if a else None,
            "assignee_id":     a.get("accountId", "") if a else None,
            "priority":        p.get("name", "") if p else "",
            "updated":         f.get("updated", ""),
            "hours_stalled":   hours,
            "blockers":        blockers,
        })
    return {"total": len(issues), "issues": issues}


def get_issue_context(issue_key):
    """Fetch deep context: description + last 5 comments."""
    data = _jira("GET", f"/issue/{issue_key}", params={
        "fields": "summary,description,comment,status,assignee,priority,updated"
    })
    if "error" in data:
        return {"description": "", "comments": []}

    f = data.get("fields", {})

    # Description (ADF → plain text)
    desc = _parse_adf_text(f.get("description")) or "(no description)"

    # Last 5 comments
    comments_raw = f.get("comment", {}).get("comments", [])
    comments = []
    for c in comments_raw[-5:]:
        author = c.get("author", {}).get("displayName", "Unknown")
        text = _parse_adf_text(c.get("body"))
        created = c.get("created", "")[:16]
        comments.append({"author": author, "text": text, "date": created})

    return {"description": desc[:1000], "comments": comments}


def post_comment_with_mention(issue_key, text, assignee_name, assignee_id):
    """Post a Jira comment with a real @mention at the start."""
    # Build ADF: @mention greeting + AI-generated paragraphs
    paragraphs = text.strip().split("\n\n")

    content = []
    # First paragraph: greeting with @mention
    greeting = [{"type": "text", "text": "Hi "}]
    if assignee_id:
        greeting.append({
            "type": "mention",
            "attrs": {"id": assignee_id, "text": f"@{assignee_name}", "accessLevel": ""}
        })
    else:
        greeting.append({"type": "text", "text": assignee_name or "team"})

    # Append first paragraph text to the greeting line
    first_para = paragraphs[0] if paragraphs else ""
    greeting.append({"type": "text", "text": f", {first_para}"})
    content.append({"type": "paragraph", "content": greeting})

    # Remaining paragraphs
    for para in paragraphs[1:]:
        if para.strip():
            content.append({"type": "paragraph", "content": [{"type": "text", "text": para.strip()}]})

    # Signature (italic)
    content.append({"type": "paragraph", "content": [
        {"type": "text", "text": "— Danielle, PMO Execution Lead", "marks": [{"type": "em"}]}
    ]})

    body = {"body": {"version": 1, "type": "doc", "content": content}}
    return _jira("POST", f"/issue/{issue_key}/comment", json_body=body)


# ══════════════════════════════════════════════════════════════
# AI — Gemini for context-aware responses
# ══════════════════════════════════════════════════════════════

def generate_ai_chase(issue, context, level):
    """Use Gemini to generate a context-aware chase comment."""
    import httpx

    comments_text = ""
    for c in context.get("comments", []):
        comments_text += f"  [{c['date']}] {c['author']}: {c['text'][:200]}\n"
    if not comments_text:
        comments_text = "  (no comments yet)\n"

    prompt = f"""You are Danielle, the PMO Execution Lead at ISRDS.
You are writing a Jira comment to chase a stalled ticket. Be professional, warm, and direct.

RULES:
- Do NOT start with "Hi [name]" — the system adds the @mention automatically.
- Start directly with the context/reason for the message.
- Keep it 2-3 short paragraphs max.
- Reference specific details from the description or last comments.
- If there are recent comments, acknowledge what was said and ask for next steps.
- If no comments, ask what's blocking progress.
- Mention how long it's been stalled (in days).
- DO NOT sign off — the system adds the signature automatically.
- This is a {'🔴 leadership escalation' if level == 'escalation' else '🟠 check-in'}.

TICKET:
  Key: {issue['key']}
  Summary: {issue['summary']}
  Status: {issue['status']}
  Priority: {issue['priority']}
  Assignee: {issue.get('assignee', 'Unassigned')}
  Stalled: {issue['hours_stalled']}h ({issue['hours_stalled'] // 24} days)

DESCRIPTION:
  {context['description'][:500]}

LAST COMMENTS:
{comments_text}

Write the chase comment now (2-3 paragraphs, no greeting, no sign-off):"""

    # Call Gemini via Vertex AI REST API
    api_url = (
        f"https://{GCP_LOCATION}-aiplatform.googleapis.com/v1beta1/"
        f"projects/{GCP_PROJECT}/locations/{GCP_LOCATION}/"
        f"publishers/google/models/{AI_MODEL}:generateContent"
    )

    try:
        # Use Application Default Credentials
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        creds.refresh(google.auth.transport.requests.Request())
        access_token = creds.token

        r = httpx.post(
            api_url,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": 0.7, "maxOutputTokens": 300},
            },
            timeout=30.0,
            verify=_SSL_VERIFY,
        )
        if r.status_code == 200:
            text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
            return text.strip()
    except Exception as e:
        log.warning(f"   ⚠️  AI generation failed: {e}. Using template fallback.")

    # Fallback to template if AI fails
    days = issue["hours_stalled"] // 24
    return (
        f"this is Danielle from the PMO.\n\n"
        f"'{issue['summary']}' hasn't been updated in {days} days. "
        f"This is a {issue['priority']} priority item"
        f"{' and I am flagging it for leadership visibility' if level == 'escalation' else ''}.\n\n"
        f"Can you please share an update today? If there's a blocker, let me know and I'll help clear the path."
    )


def ledger_log(event_type, detail):
    """Append to the trust ledger."""
    with open(LEDGER_PATH, "a") as fp:
        fp.write(json.dumps({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "detail": detail[:500],
        }) + "\n")


# ══════════════════════════════════════════════════════════════
# PMO Cycle
# ══════════════════════════════════════════════════════════════

def scan_board():
    """Full board scan."""
    log.info("📋 Scanning the board...")
    result = run_jql(
        f'project = "{JIRA_PROJECT}" AND statusCategory != Done ORDER BY priority DESC, updated ASC',
        max_results=100,
    )
    if "error" in result:
        log.error(f"Board scan failed: {result['error']}")
        return result

    issues = result["issues"]
    in_progress = [i for i in issues if i["status_category"] == "In Progress"]
    to_do       = [i for i in issues if i["status_category"] == "To Do"]
    unassigned  = [i for i in issues if not i.get("assignee")]
    critical    = [i for i in issues if i.get("priority") in ("Critical", "Highest", "High", "1 - Urgent")]

    by_assignee = {}
    for i in issues:
        by_assignee.setdefault(i.get("assignee") or "Unassigned", []).append(i)

    log.info(f"   Active: {len(issues)} | In Progress: {len(in_progress)} | "
             f"To Do: {len(to_do)} | Unassigned: {len(unassigned)} | Critical: {len(critical)}")

    return {
        "total": len(issues), "issues": issues,
        "in_progress": len(in_progress), "to_do": len(to_do),
        "unassigned": unassigned, "critical": critical,
        "by_assignee": by_assignee,
    }


def find_stalled():
    """Find stalled tickets and categorize by severity."""
    log.info(f"🔍 Finding stalled issues (>{STALE_HOURS}h)...")
    result = run_jql(
        f'project = "{JIRA_PROJECT}" AND statusCategory != Done '
        f'AND updated <= "-{STALE_HOURS}h" ORDER BY priority DESC, updated ASC',
        max_results=50,
    )
    if "error" in result:
        log.error(f"Stalled scan failed: {result['error']}")
        return result

    stalled = result["issues"]
    return {
        "stalled":          stalled,
        "needs_chase":      [i for i in stalled if i["hours_stalled"] >= CHASE_HOURS],
        "needs_escalation": [i for i in stalled if i["hours_stalled"] >= ESCALATE_HOURS],
    }


def check_changes(hours=24):
    """Check what changed in the last N hours."""
    log.info(f"🔄 Checking changes in last {hours}h...")
    result = run_jql(
        f'project = "{JIRA_PROJECT}" AND updated >= "-{hours}h" ORDER BY updated DESC',
        max_results=50,
    )
    if "error" in result:
        log.error(f"Changes check failed: {result['error']}")
        return result

    issues = result["issues"]
    resolved = [i["key"] for i in issues if i.get("status_category") == "Done"]
    log.info(f"   Changed: {len(issues)} | Resolved: {len(resolved)}")
    return {"total_changed": len(issues), "resolved": resolved}


def auto_chase(stalled_data):
    """Read context → AI generates comment → post with @mention."""
    if not AUTO_COMMENT:
        log.info("⏸️  Auto-comment OFF (set PMO_AUTO_COMMENT=true)")
        return []

    chases = []
    for issue in stalled_data.get("needs_chase", []):
        key       = issue["key"]
        assignee  = issue.get("assignee") or "team"
        a_id      = issue.get("assignee_id")
        hours     = issue["hours_stalled"]
        days      = hours // 24

        if hours >= ESCALATE_HOURS:
            level, label = "escalation", "🔴 ESCALATION"
        elif hours >= CHASE_HOURS:
            level, label = "chase", "🟠 CHASE"
        else:
            continue

        # 1. Read description + last comments for context
        log.info(f"   📖 Reading context for {key}...")
        context = get_issue_context(key)

        # 2. AI generates a context-aware response
        log.info(f"   🤖 Generating AI response for {key}...")
        ai_text = generate_ai_chase(issue, context, level)

        # 3. Post with real @mention
        result = post_comment_with_mention(key, ai_text, assignee, a_id)
        if "error" not in result:
            log.info(f"   💬 {label}: Commented on {key} → @{assignee} ({days}d stalled)")
            ledger_log("auto-chase", f"{label} on {key} → @{assignee} ({hours}h). AI text: {ai_text[:200]}")
            chases.append({"key": key, "assignee": assignee, "hours": hours, "days": days, "level": label})
        else:
            log.warning(f"   ⚠️  Failed on {key}: {result['error']}")

    return chases


# ══════════════════════════════════════════════════════════════
# Operating Brief
# ══════════════════════════════════════════════════════════════

def generate_brief(board, stalled, changes, chases):
    """Generate the Operating Brief."""
    now = datetime.now()
    stalled_list = stalled.get("stalled", [])
    unassigned   = board.get("unassigned", [])
    resolved     = changes.get("resolved", [])

    L = []  # Lines
    L.append(f"ISRDS Operating Brief — {now.strftime('%A, %B %d, %Y at %I:%M %p')}")
    L.append("Prepared by Danielle, PMO Execution Lead")
    L.append("")
    L.append("=" * 60)

    # Board snapshot
    L.append("")
    L.append("BOARD SNAPSHOT")
    L.append(f"  Active: {board.get('total', 0)} | In Progress: {board.get('in_progress', 0)} | To Do: {board.get('to_do', 0)}")
    L.append(f"  Unassigned: {len(unassigned)} | Critical/High: {len(board.get('critical', []))}")

    # Team workload
    L.append("")
    L.append("TEAM WORKLOAD")
    for owner, tickets in sorted(board.get("by_assignee", {}).items(), key=lambda x: -len(x[1])):
        stalled_n = sum(1 for t in tickets if t["hours_stalled"] > STALE_HOURS)
        flag = f" ⚠️ {stalled_n} stalled" if stalled_n else ""
        L.append(f"  {owner}: {len(tickets)} tickets{flag}")

    # Attention needed
    if stalled_list:
        L.append("")
        L.append("🔴 NEEDS ATTENTION")
        for i, issue in enumerate(stalled_list[:10], 1):
            d = issue["hours_stalled"] // 24
            L.append(f"  {i}. {issue['key']} — {issue['summary']}")
            L.append(f"     {issue.get('assignee') or 'UNASSIGNED'} | {d}d stalled | {issue.get('priority', '?')}")

    # Ownership gaps
    if unassigned:
        L.append("")
        L.append("🟡 OWNERSHIP GAPS")
        for issue in unassigned[:5]:
            L.append(f"  {issue['key']} — {issue['summary']}")

    # Changes
    L.append("")
    L.append("📝 CHANGES (24h)")
    L.append(f"  Resolved: {', '.join(resolved) if resolved else 'None'}")
    L.append(f"  Total activity: {changes.get('total_changed', 0)} tickets")

    # Chases
    if chases:
        L.append("")
        L.append(f"💬 CHASES POSTED ({len(chases)})")
        for c in chases:
            L.append(f"  {c['level']} → @{c['assignee']} on {c['key']} ({c['days']}d)")

    # Assessment
    L.append("")
    L.append("ASSESSMENT")
    esc = len(stalled.get("needs_escalation", []))
    if not stalled_list:
        L.append("  ✅ Board healthy. No stalled tickets.")
    elif len(stalled_list) <= 3:
        L.append(f"  ⚠️ {len(stalled_list)} stalled — manageable.")
    else:
        L.append(f"  🔴 {len(stalled_list)} stalled — team needs unblocking.")
    if esc:
        L.append(f"  🚨 {esc} tickets >{ESCALATE_HOURS}h — recommending escalation.")
    if unassigned:
        L.append(f"  📌 {len(unassigned)} tickets without owners.")

    L.append("")
    L.append("=" * 60)
    L.append("— Danielle")
    return "\n".join(L)


# ══════════════════════════════════════════════════════════════
# Execution
# ══════════════════════════════════════════════════════════════

def run_cycle():
    """Execute one full PMO cycle."""
    start = datetime.now()
    log.info("=" * 60)
    log.info(f"🚀 Cycle starting at {start.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info("=" * 60)

    board = scan_board()
    if "error" in board:
        log.error("❌ Board scan failed. Skipping.")
        return

    stalled = find_stalled()
    if "error" in stalled:
        stalled = {"stalled": [], "needs_chase": [], "needs_escalation": []}

    changes = check_changes(hours=24)
    if "error" in changes:
        changes = {"total_changed": 0, "resolved": []}

    chases = auto_chase(stalled)
    brief  = generate_brief(board, stalled, changes, chases)

    # Save brief
    ts = start.strftime("%Y%m%d_%H%M%S")
    (BRIEF_DIR / f"brief_{ts}.txt").write_text(brief)
    (BRIEF_DIR / "latest.txt").write_text(brief)

    log.info(f"📄 Brief → briefs/brief_{ts}.txt")
    log.info(f"📊 Summary: {board.get('total',0)} active, {len(stalled.get('stalled',[]))} stalled, "
             f"{len(chases)} chases, {(datetime.now()-start).total_seconds():.1f}s")
    print("\n" + brief + "\n")
    ledger_log("cycle", f"{board.get('total',0)} active, {len(stalled.get('stalled',[]))} stalled, {len(chases)} chases")


def run_daemon():
    """Run the daemon loop."""
    log.info("=" * 60)
    log.info(f"🤖 PMO Daemon | {JIRA_PROJECT} @ {JIRA_URL}")
    log.info(f"   Scan: {SCAN_INTERVAL}min | Chase: {CHASE_HOURS}h | Escalate: {ESCALATE_HOURS}h | AI: {AI_MODEL}")
    log.info(f"   Auto-comment: {AUTO_COMMENT} | Brief: {BRIEF_HOUR}:00")
    log.info("=" * 60)

    last_brief_date = None
    while True:
        try:
            run_cycle()
            now = datetime.now()
            if now.hour == BRIEF_HOUR and (not last_brief_date or last_brief_date != now.date()):
                log.info("📧 Daily brief generated.")
                last_brief_date = now.date()
            log.info(f"⏰ Next in {SCAN_INTERVAL}min...")
            time.sleep(SCAN_INTERVAL * 60)
        except KeyboardInterrupt:
            log.info("🛑 Stopped.")
            break
        except Exception as e:
            log.error(f"❌ {type(e).__name__}: {e}")
            time.sleep(SCAN_INTERVAL * 60)


if __name__ == "__main__":
    if "--once" in sys.argv:
        run_cycle()
    elif "--brief" in sys.argv:
        b, s, c = scan_board(), find_stalled(), check_changes(24)
        print(generate_brief(b, s, c, []))
    else:
        run_daemon()

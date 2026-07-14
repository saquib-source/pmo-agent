"""
Shared Jira client for the PMO swarm.
All agents import from here — one authenticated HTTP client, one extraction contract.
"""
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path


# ── Config ──────────────────────────────────────────────────────────────────

# Last-resort list, used only when live discovery is impossible (no Jira creds
# in the environment, or the API call fails). Production sets JIRA_PROJECTS=ALL
# and discovers the real list live, so new Jira projects are picked up
# automatically on the next run — no redeploy.
_STATIC_FALLBACK_PROJECTS = ["ASHS", "BAS", "BTK", "FQ", "ISRDS", "MDP", "SOC", "UNCS"]


def discover_all_projects() -> list:
    """Every project key this token can see, live from Jira
    (GET /project/search, paginated). Returns [] on any error so callers can
    fall back — never raises."""
    keys, start = [], 0
    while True:
        data = jira_request("GET", "/project/search",
                            params={"startAt": start, "maxResults": 100})
        if not isinstance(data, dict) or "error" in data:
            return []
        values = data.get("values", [])
        keys.extend(p.get("key") for p in values if p.get("key"))
        if data.get("isLast", True) or not values:
            break
        start += len(values)
    return sorted(set(keys))


def _get_allowed_projects() -> list:
    """The projects this swarm scans.

    JIRA_PROJECTS env:
      • "ALL" or "*" or unset → live discovery of every project the token can
        see (falls back to the static list only if discovery fails)
      • explicit CSV ("ISRDS,BAS") → exactly that list
    """
    raw = os.environ.get("JIRA_PROJECTS", "").strip()
    if raw and raw.upper() not in ("ALL", "*"):
        explicit = [p.strip() for p in raw.split(",") if p.strip()]
        if not any(p.upper() in ("ALL", "*") for p in explicit):
            return explicit
    live = discover_all_projects()
    return live or list(_STATIC_FALLBACK_PROJECTS)

# NOTE: ALL_PROJECTS is assigned after jira_request is defined (below) —
# discovery calls it at import time.

RACI_FIELDS = {
    "accountable":  "customfield_11661",
    "responsible":  "customfield_11657",
    "consulted":    "customfield_11536",
    "informed":     "customfield_11665",
    "division":     "customfield_11622",
    "department":   "customfield_11623",
    "eass_rating":  "customfield_11655",
    "product_level":"customfield_11666",
    "feature_set":  "customfield_11654",
    "phase":        "customfield_11873",
    "budget":       "customfield_11806",
}

HYGIENE_ISSUE_TYPE = "Configured Component"

# Follow-up eligibility policy.
# Danielle only chases work that is genuinely "in flight": assigned to an active
# sprint AND in the In-Progress status category. This deliberately excludes:
#   • Backlog tickets       — not in any active sprint → no sprint commitment to chase
#   • Done tickets          — work is complete; commenting is noise
#   • To Do tickets         — not started; not yet a "stall", nothing to follow up on
# These rules are enforced both at discovery time (find_stalled JQL) and as a hard
# write-time guard (is_comment_eligible) so a misbehaving agent cannot bypass them.
ELIGIBLE_STATUS_CATEGORY = "In Progress"   # Jira statusCategory.name for active work
SKIP_STATUS_CATEGORIES   = ("Done", "To Do")  # complete, or not-yet-started


def _config():
    return (
        os.environ.get("JIRA_URL", ""),
        os.environ.get("JIRA_EMAIL", ""),
        os.environ.get("JIRA_API_TOKEN", ""),
        os.environ.get("JIRA_PROJECT", "ISRDS"),
    )


def _ssl():
    return os.environ.get("SSL_CERT_FILE") or True


# ── HTTP ─────────────────────────────────────────────────────────────────────

def jira_request(method: str, path: str, params: dict = None, json_body: dict = None) -> dict:
    import httpx
    url, email, token, _ = _config()
    if not url or not email or not token:
        return {"error": "Jira not configured — set JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN"}
    try:
        with httpx.Client(
            auth=(email, token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
            verify=_ssl(),
            follow_redirects=True,
        ) as c:
            r = c.request(method, f"{url}/rest/api/3{path}", params=params, json=json_body)
            if r.status_code == 204:
                return {"success": True}
            if r.status_code == 401:
                return {"error": "Jira auth failed — check JIRA_EMAIL and JIRA_API_TOKEN"}
            if r.status_code == 404:
                return {"error": f"Not found: {path}"}
            if r.status_code >= 400:
                return {"error": f"Jira {r.status_code}: {r.text[:400]}"}
            return r.json()
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# Evaluated at import time — agents reference this for the "ALL" shorthand.
# Because JIRA_PROJECTS is set in .env before any import, this is safe. The
# Cloud Run job is a fresh process per scheduled run, so the discovered list
# is re-resolved daily.
ALL_PROJECTS = _get_allowed_projects()


# ── Extraction ───────────────────────────────────────────────────────────────

def _hours_since(iso_str: str) -> int:
    if not iso_str:
        return 0
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return int((datetime.now(timezone.utc) - dt).total_seconds() / 3600)
    except (ValueError, TypeError):
        return 0


def _parse_adf(adf_body: dict) -> str:
    """Extract plain text from Atlassian Document Format."""
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


def extract_issue(raw: dict) -> dict:
    """Normalise a raw Jira issue dict into the PMO contract."""
    fields = raw.get("fields", {})
    assignee  = fields.get("assignee")
    reporter  = fields.get("reporter")
    status    = fields.get("status")
    priority  = fields.get("priority")
    updated   = fields.get("updated", "")

    blockers = []
    for link in fields.get("issuelinks", []):
        if link.get("type", {}).get("name") == "Blocks":
            target = link.get("inwardIssue") or link.get("outwardIssue")
            if target:
                blockers.append(target["key"])

    comments = []
    for c in fields.get("comment", {}).get("comments", [])[-5:]:
        comments.append({
            "author":  c.get("author", {}).get("displayName", ""),
            "created": c.get("created", ""),
            "body":    _parse_adf(c.get("body"))[:300],
        })

    jira_url, _, _, _ = _config()
    return {
        "key":             raw.get("key", ""),
        "summary":         fields.get("summary", ""),
        "status":          status.get("name", "") if status else "",
        "status_category": status.get("statusCategory", {}).get("name", "") if status else "",
        "assignee":        assignee.get("displayName", "") if assignee else None,
        "assignee_id":     assignee.get("accountId", "") if assignee else None,
        "reporter":        reporter.get("displayName", "") if reporter else None,
        "priority":        priority.get("name", "") if priority else "",
        "labels":          fields.get("labels", []),
        "issue_type":      fields.get("issuetype", {}).get("name", ""),
        "epic_link":       fields.get("parent", {}).get("key") if fields.get("parent") else None,
        "created":         fields.get("created", ""),
        "updated":         updated,
        "hours_stalled":   _hours_since(updated),
        "blockers":        blockers,
        "comments":        comments,
        "url":             f"{jira_url}/browse/{raw.get('key', '')}",
    }


# ── Core read operations ─────────────────────────────────────────────────────

def run_jql(jql: str, max_results: int = 50, extra_fields: list = None) -> dict:
    """Run JQL, return list of extracted issues."""
    base_fields = ["summary", "status", "assignee", "reporter", "priority", "labels",
                   "created", "updated", "issuelinks", "issuetype", "parent"]
    fields = base_fields + (extra_fields or [])

    all_issues = []
    next_page = None
    while True:
        body = {"jql": jql, "maxResults": min(max_results - len(all_issues), 100), "fields": fields}
        if next_page:
            body["nextPageToken"] = next_page
        data = jira_request("POST", "/search/jql", json_body=body)
        if "error" in data:
            return data
        batch = data.get("issues", [])
        all_issues.extend(batch)
        if data.get("isLast", True) or len(all_issues) >= max_results:
            break
        next_page = data.get("nextPageToken")
        if not next_page:
            break

    return {"total": len(all_issues), "issues": [extract_issue(i) for i in all_issues], "jql": jql}


def get_issue_detail(issue_key: str) -> dict:
    """Fetch a single issue with changelog and full comments."""
    data = jira_request("GET", f"/issue/{issue_key}", params={
        "expand": "changelog",
        "fields": "summary,description,status,assignee,reporter,priority,labels,"
                  "created,updated,issuelinks,comment,issuetype,parent,"
                  + ",".join(RACI_FIELDS.values()),
    })
    if "error" in data:
        return data

    issue = extract_issue(data)
    fields = data.get("fields", {})

    # Full comment list (not just last 5)
    all_comments = []
    for c in fields.get("comment", {}).get("comments", []):
        all_comments.append({
            "author":  c.get("author", {}).get("displayName", ""),
            "created": c.get("created", ""),
            "body":    _parse_adf(c.get("body"))[:500],
        })
    issue["all_comments"] = all_comments

    # Description
    issue["description"] = _parse_adf(fields.get("description"))[:1000]

    # Changelog
    changelog = []
    for hist in data.get("changelog", {}).get("histories", [])[-20:]:
        for item in hist.get("items", []):
            changelog.append({
                "timestamp": hist.get("created", ""),
                "author":    hist.get("author", {}).get("displayName", ""),
                "field":     item.get("field", ""),
                "from":      item.get("fromString", ""),
                "to":        item.get("toString", ""),
            })
    issue["changelog"] = changelog

    # RACI custom fields
    issue["raci"] = _extract_raci(fields)

    return issue


def _extract_raci(fields: dict) -> dict:
    """Pull RACI and product-architecture custom fields from a raw fields dict."""
    def _user(val):
        if isinstance(val, dict):
            return {"name": val.get("displayName", ""), "id": val.get("accountId", "")}
        return None

    def _users(val):
        if isinstance(val, list):
            return [_user(u) for u in val if isinstance(u, dict)]
        return []

    return {
        "accountable":   _user(fields.get(RACI_FIELDS["accountable"])),
        "responsible":   _user(fields.get(RACI_FIELDS["responsible"])),
        "consulted":     _users(fields.get(RACI_FIELDS["consulted"])),
        "informed":      _users(fields.get(RACI_FIELDS["informed"])),
        "division":      fields.get(RACI_FIELDS["division"]),
        "department":    fields.get(RACI_FIELDS["department"]),
        "eass_rating":   fields.get(RACI_FIELDS["eass_rating"]),
        "product_level": fields.get(RACI_FIELDS["product_level"]),
        "feature_set":   fields.get(RACI_FIELDS["feature_set"]),
        "phase":         fields.get(RACI_FIELDS["phase"]),
        "budget":        fields.get(RACI_FIELDS["budget"]),
    }


# ── Follow-up eligibility (write-time guard) ──────────────────────────────────

# Sprint custom-field id. Detected for this instance (lixillabs) as customfield_10007;
# overridable via JIRA_SPRINT_FIELD for other instances. Run detect_sprint_field.py to find it.
SPRINT_FIELD = os.environ.get("JIRA_SPRINT_FIELD", "customfield_10007")


def _in_open_sprint(sprint_val) -> bool:
    """True if the issue is assigned to at least one currently-active sprint.

    The sprint field is a list of sprint objects (or GreenHopper strings on older
    instances). A ticket with no sprint, or only closed/future sprints, is backlog
    work and must not be chased.
    """
    if not sprint_val:
        return False
    if isinstance(sprint_val, dict):
        sprint_val = [sprint_val]
    if not isinstance(sprint_val, list):
        sprint_val = [sprint_val]
    for s in sprint_val:
        if isinstance(s, dict):
            if str(s.get("state", "")).lower() == "active":
                return True
        elif isinstance(s, str) and "state=ACTIVE" in s:
            return True
    return False


def is_comment_eligible(issue_key: str) -> dict:
    """Hard gate: may Danielle comment on / chase this ticket?

    Re-fetches the live ticket and enforces the follow-up policy regardless of how
    the caller discovered it. Returns {"eligible": bool, "reason": str, "status": str}.

    Blocks when the ticket is:
      • Done or To Do (by statusCategory), or
      • not in any currently-active sprint (i.e. backlog).
    """
    data = jira_request("GET", f"/issue/{issue_key}", params={
        "fields": f"status,{SPRINT_FIELD}",
    })
    if "error" in data:
        # Fail closed — if we can't verify eligibility, don't comment.
        return {"eligible": False, "reason": f"could not verify ticket: {data['error']}", "status": ""}

    fields   = data.get("fields", {})
    status   = fields.get("status", {}) or {}
    category = (status.get("statusCategory", {}) or {}).get("name", "")
    status_n = status.get("name", "")

    if category in SKIP_STATUS_CATEGORIES:
        kind = "Done" if category == "Done" else "To Do"
        return {"eligible": False,
                "reason": f"ticket is {kind} (status '{status_n}') — policy: do not comment on {kind} tickets",
                "status": status_n}

    if not _in_open_sprint(fields.get(SPRINT_FIELD)):
        return {"eligible": False,
                "reason": "ticket is in the backlog (no active sprint) — policy: do not follow up on backlog tickets",
                "status": status_n}

    return {"eligible": True, "reason": "in active sprint and in progress", "status": status_n}


# ── Write operations ─────────────────────────────────────────────────────────

def add_comment_adf(issue_key: str, text: str, assignee_name: str = None,
                    assignee_id: str = None) -> dict:
    """
    Post a Jira comment. If assignee_name+id are provided the comment opens
    with a proper ADF @mention; otherwise falls back to plain text.
    """
    from .governance import trust_ledger_log

    # Single enforcement chokepoint. Every posting path — the follow-up agent, the
    # CLI approver, and the UI approver — funnels through here, so the eligibility
    # policy (no Done/To Do, no backlog) holds with or without human approval.
    elig = is_comment_eligible(issue_key)
    if not elig["eligible"]:
        trust_ledger_log("skip-comment", f"Blocked comment on {issue_key}: {elig['reason']}",
                         agent_id="pmo_follow_up")
        return {"skipped": True, "issue_key": issue_key, "reason": elig["reason"],
                "message": f"Comment not posted — {elig['reason']}."}

    paragraphs = text.strip().split("\n\n")
    content = []

    if assignee_id and assignee_name:
        greeting = [
            {"type": "text", "text": "Hi "},
            {"type": "mention", "attrs": {"id": assignee_id,
                                          "text": f"@{assignee_name}",
                                          "accessLevel": ""}},
            {"type": "text", "text": f", {paragraphs[0]}"},
        ]
        content.append({"type": "paragraph", "content": greeting})
        for para in paragraphs[1:]:
            if para.strip():
                content.append({"type": "paragraph",
                                 "content": [{"type": "text", "text": para.strip()}]})
    else:
        for para in paragraphs:
            if para.strip():
                content.append({"type": "paragraph",
                                 "content": [{"type": "text", "text": para.strip()}]})

    content.append({"type": "paragraph", "content": [
        {"type": "text", "text": "— Delivery Agent", "marks": [{"type": "em"}]}
    ]})

    result = jira_request("POST", f"/issue/{issue_key}/comment",
                          json_body={"body": {"version": 1, "type": "doc", "content": content}})
    if "error" in result:
        return result

    trust_ledger_log("jira-comment", f"Commented on {issue_key}: {text[:100]}", agent_id="pmo_follow_up")
    return {"success": True, "issue_key": issue_key, "comment_id": result.get("id", "")}


def get_transitions(issue_key: str) -> dict:
    return jira_request("GET", f"/issue/{issue_key}/transitions")


def transition_issue(issue_key: str, transition_name: str, agent_id: str = "pmo_execution") -> dict:
    from .governance import trust_ledger_log
    transitions = get_transitions(issue_key)
    if "error" in transitions:
        return transitions

    target = None
    available = []
    for t in transitions.get("transitions", []):
        available.append(t["name"])
        if t["name"].lower() == transition_name.lower():
            target = t

    if not target:
        return {"error": f"Transition '{transition_name}' not found. Available: {available}"}

    result = jira_request("POST", f"/issue/{issue_key}/transitions",
                          json_body={"transition": {"id": target["id"]}})
    if "error" in result:
        return result

    trust_ledger_log("jira-transition", f"Moved {issue_key} → {transition_name}", agent_id=agent_id)
    return {"success": True, "issue_key": issue_key, "transition": transition_name}


def assign_issue(issue_key: str, account_id: str = None, agent_id: str = "pmo_raci") -> dict:
    from .governance import trust_ledger_log
    result = jira_request("PUT", f"/issue/{issue_key}/assignee",
                          json_body={"accountId": account_id})
    if "error" in result:
        return result
    trust_ledger_log("jira-assign", f"Assigned {issue_key} → {account_id or 'Unassigned'}",
                     agent_id=agent_id)
    return {"success": True, "issue_key": issue_key}


# ── People ───────────────────────────────────────────────────────────────────

def find_user(query: str) -> dict:
    data = jira_request("GET", "/user/search", params={"query": query, "maxResults": 10})
    if "error" in data:
        return data
    users = data if isinstance(data, list) else data.get("values", [])
    return {"users": [{"displayName": u.get("displayName", ""),
                        "accountId":   u.get("accountId", ""),
                        "email":       u.get("emailAddress", "")} for u in users]}


def get_project_members(project: str = "") -> dict:
    proj = project or _config()[3]
    data = jira_request("GET", "/user/assignable/search",
                         params={"project": proj, "maxResults": 100})
    if "error" in data:
        return data
    users = data if isinstance(data, list) else []
    return {"project": proj,
            "members": [{"displayName": u.get("displayName", ""),
                          "accountId":   u.get("accountId", ""),
                          "email":       u.get("emailAddress", "")} for u in users]}


# ── Stall / activity helpers ─────────────────────────────────────────────────

def find_stalled(hours_threshold: int = 24, projects: list = None) -> dict:
    proj_list = projects or ALL_PROJECTS
    proj_jql  = " OR ".join(f'project = "{p}"' for p in proj_list)
    cutoff    = (datetime.now(timezone.utc) - timedelta(hours=hours_threshold)).strftime("%Y-%m-%d %H:%M")

    # Only chase work that is in an OPEN sprint and actively In Progress.
    # `sprint in openSprints()` excludes backlog (no active sprint commitment);
    # `statusCategory = "In Progress"` excludes both Done and To Do tickets.
    # See ELIGIBLE_STATUS_CATEGORY / SKIP_STATUS_CATEGORIES for the policy rationale.
    jql = (f'({proj_jql}) AND sprint in openSprints() '
           f'AND statusCategory = "{ELIGIBLE_STATUS_CATEGORY}" '
           f'AND updated <= "{cutoff}" ORDER BY priority DESC, updated ASC')
    result = run_jql(jql, max_results=100)
    if "error" in result:
        return result

    stalled  = result["issues"]
    critical = [i for i in stalled if i.get("priority") in ("Critical", "Highest", "High", "1 - Urgent")]
    orphaned = [i for i in stalled if not i.get("assignee")]
    blocking = [i for i in stalled if i.get("blockers")]

    return {
        "hours_threshold": hours_threshold,
        "total_stalled":   len(stalled),
        "critical":        [i["key"] for i in critical],
        "no_owner":        [i["key"] for i in orphaned],
        "blocking_others": [i["key"] for i in blocking],
        "issues":          stalled,
    }


def get_changes_since(hours: int = 24, projects: list = None) -> dict:
    proj_list = projects or ALL_PROJECTS
    proj_jql  = " OR ".join(f'project = "{p}"' for p in proj_list)
    cutoff    = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M")

    jql = f'({proj_jql}) AND updated >= "{cutoff}" ORDER BY updated DESC'
    result = run_jql(jql, max_results=100)
    if "error" in result:
        return result

    issues   = result["issues"]
    resolved = [i["key"] for i in issues if i.get("status_category") == "Done"]
    return {"hours": hours, "total_changed": len(issues), "resolved": resolved, "issues": issues}

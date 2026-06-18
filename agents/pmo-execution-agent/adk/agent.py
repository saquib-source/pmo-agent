"""
ISRDS PMO Execution Agent — Google ADK Runtime (v3.0)

Full PMO agent with Jira read/write, JQL, commenting, and human-like behavior.
Uses Vertex AI with service account for Gemini access.

Local test:  cd agents/pmo-execution-agent/adk && adk web .
"""
import os
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Optional

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from .governance import trust_ledger_log

# ── Load prompt from portable artifact ──
AGENT_DIR = Path(__file__).parent.parent
PROMPT = (AGENT_DIR / "prompt.md").read_text()

# ── Layer 1: Runtime engine ──
MODEL = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")

# ── Jira Config (read dynamically so daemon can load .env first) ──
def _jira_config():
    return (
        os.environ.get("JIRA_URL", ""),
        os.environ.get("JIRA_EMAIL", ""),
        os.environ.get("JIRA_API_TOKEN", ""),
        os.environ.get("JIRA_PROJECT", "ISRDS"),
    )

# Module-level shortcuts (for ADK web mode where env is loaded by ADK)
JIRA_URL = os.environ.get("JIRA_URL", "")
JIRA_EMAIL = os.environ.get("JIRA_EMAIL", "")
JIRA_API_TOKEN = os.environ.get("JIRA_API_TOKEN", "")
JIRA_PROJECT = os.environ.get("JIRA_PROJECT", "ISRDS")


def _jira_request(method: str, path: str, params: dict = None, json_body: dict = None) -> dict:
    """Make an authenticated Jira REST API request."""
    import httpx
    jira_url, jira_email, jira_token, _ = _jira_config()
    if not jira_url or not jira_email or not jira_token:
        return {"error": "Jira not configured. Set JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN in .env"}

    url = f"{jira_url}/rest/api/3{path}"
    try:
        with httpx.Client(
            auth=(jira_email, jira_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
            verify=os.environ.get("SSL_CERT_FILE") or True,  # Local: custom cert; Production: system default
            follow_redirects=True,
        ) as client:
            resp = client.request(method, url, params=params, json=json_body)
            if resp.status_code == 401:
                return {"error": "Authentication failed. Check JIRA_EMAIL and JIRA_API_TOKEN."}
            if resp.status_code == 404:
                return {"error": f"Not found: {path}. Check JIRA_URL and the issue/project key."}
            if resp.status_code >= 400:
                return {"error": f"Jira API error {resp.status_code}: {resp.text[:500]}"}
            # Some endpoints return 204 No Content
            if resp.status_code == 204:
                return {"success": True}
            return resp.json()
    except httpx.ConnectError as e:
        return {"error": f"Cannot connect to Jira ({jira_url}). Check URL and network. Detail: {e}"}
    except Exception as e:
        return {"error": f"Jira request failed: {type(e).__name__}: {e}"}


def _extract_issue(issue: dict) -> dict:
    """Extract key fields from a Jira issue."""
    fields = issue.get("fields", {})
    assignee = fields.get("assignee")
    status = fields.get("status")
    priority = fields.get("priority")
    reporter = fields.get("reporter")
    updated = fields.get("updated", "")

    hours_since_update = 0
    if updated:
        try:
            updated_dt = datetime.fromisoformat(updated.replace("Z", "+00:00"))
            hours_since_update = int((datetime.now(timezone.utc) - updated_dt).total_seconds() / 3600)
        except (ValueError, TypeError):
            pass

    blockers = []
    for link in fields.get("issuelinks", []):
        if link.get("type", {}).get("name") == "Blocks":
            if "inwardIssue" in link:
                blockers.append(link["inwardIssue"]["key"])
            if "outwardIssue" in link:
                blockers.append(link["outwardIssue"]["key"])

    # Extract latest comments
    comments = []
    comment_data = fields.get("comment", {})
    if isinstance(comment_data, dict):
        for c in comment_data.get("comments", [])[-5:]:
            body_parts = []
            for block in c.get("body", {}).get("content", []):
                for inline in block.get("content", []):
                    if inline.get("type") == "text":
                        body_parts.append(inline.get("text", ""))
            comments.append({
                "author": c.get("author", {}).get("displayName", ""),
                "created": c.get("created", ""),
                "body": " ".join(body_parts)[:300],
            })

    return {
        "key": issue.get("key", ""),
        "summary": fields.get("summary", ""),
        "status": status.get("name", "") if status else "",
        "status_category": status.get("statusCategory", {}).get("name", "") if status else "",
        "assignee": assignee.get("displayName", "") if assignee else None,
        "reporter": reporter.get("displayName", "") if reporter else None,
        "priority": priority.get("name", "") if priority else "",
        "labels": fields.get("labels", []),
        "issue_type": fields.get("issuetype", {}).get("name", ""),
        "created": fields.get("created", ""),
        "updated": updated,
        "hours_since_update": hours_since_update,
        "blockers": blockers,
        "comments": comments,
        "url": f"{_jira_config()[0]}/browse/{issue.get('key', '')}",
    }


# ══════════════════════════════════════════════
# Internal functions (callable by each other)
# ══════════════════════════════════════════════

def _run_jql(jql: str, max_results: int = 50, fields: list = None) -> dict:
    """Run any JQL query against Jira."""
    if not fields:
        # Note: 'comment' excluded from bulk queries for speed. Use get_issue for comments.
        fields = ["summary", "status", "assignee", "priority", "labels",
                  "created", "updated", "issuelinks", "reporter", "issuetype"]

    all_issues = []
    next_page_token = None

    while True:
        body = {
            "jql": jql,
            "maxResults": min(max_results - len(all_issues), 100),
            "fields": fields,
        }
        if next_page_token:
            body["nextPageToken"] = next_page_token

        data = _jira_request("POST", "/search/jql", json_body=body)
        if "error" in data:
            return data

        batch = data.get("issues", [])
        all_issues.extend(batch)

        # Check if there are more pages and we haven't hit our limit
        if data.get("isLast", True) or len(all_issues) >= max_results:
            break
        next_page_token = data.get("nextPageToken")
        if not next_page_token:
            break

    issues = [_extract_issue(i) for i in all_issues]
    return {"total": len(issues), "returned": len(issues), "jql": jql, "issues": issues}


def _get_issue(issue_key: str) -> dict:
    """Get a single Jira issue with full details, changelog, and comments."""
    data = _jira_request("GET", f"/issue/{issue_key}", params={
        "expand": "changelog",
        "fields": "summary,description,status,assignee,reporter,priority,labels,created,updated,issuelinks,comment,issuetype",
    })
    if "error" in data:
        return data

    issue = _extract_issue(data)
    changelog = []
    for history in data.get("changelog", {}).get("histories", [])[-15:]:
        for item in history.get("items", []):
            changelog.append({
                "timestamp": history.get("created", ""),
                "author": history.get("author", {}).get("displayName", ""),
                "field": item.get("field", ""),
                "from": item.get("fromString", ""),
                "to": item.get("toString", ""),
            })
    issue["changelog"] = changelog
    return issue


def _add_comment(issue_key: str, comment_text: str) -> dict:
    """Add a comment to a Jira ticket (as the PMO agent)."""
    # Atlassian Document Format (ADF)
    body = {
        "body": {
            "version": 1,
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": comment_text}
                    ]
                }
            ]
        }
    }
    result = _jira_request("POST", f"/issue/{issue_key}/comment", json_body=body)
    if "error" in result:
        return result
    trust_ledger_log("jira-comment", f"Commented on {issue_key}: {comment_text[:100]}")
    return {
        "success": True,
        "issue_key": issue_key,
        "comment_id": result.get("id", ""),
        "message": f"Comment posted to {issue_key}",
    }


def _get_transitions(issue_key: str) -> dict:
    """Get available workflow transitions for a ticket."""
    return _jira_request("GET", f"/issue/{issue_key}/transitions")


def _transition_issue(issue_key: str, transition_name: str) -> dict:
    """Move a ticket through its workflow (e.g., 'In Progress' → 'Done')."""
    # First get available transitions
    transitions = _get_transitions(issue_key)
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

    result = _jira_request("POST", f"/issue/{issue_key}/transitions", json_body={
        "transition": {"id": target["id"]}
    })
    if "error" in result:
        return result

    trust_ledger_log("jira-transition", f"Moved {issue_key} → {transition_name}")
    return {"success": True, "issue_key": issue_key, "transition": transition_name}


def _assign_issue(issue_key: str, account_id: str = None) -> dict:
    """Assign a ticket to someone (or unassign with None)."""
    result = _jira_request("PUT", f"/issue/{issue_key}/assignee", json_body={
        "accountId": account_id
    })
    if "error" in result:
        return result
    trust_ledger_log("jira-assign", f"Assigned {issue_key} → {account_id or 'Unassigned'}")
    return {"success": True, "issue_key": issue_key}


def _find_user(query: str) -> dict:
    """Search for Jira users by name or email."""
    data = _jira_request("GET", "/user/search", params={"query": query, "maxResults": 10})
    if "error" in data:
        return data
    if isinstance(data, list):
        return {"users": [{"displayName": u.get("displayName", ""), "accountId": u.get("accountId", ""),
                           "email": u.get("emailAddress", "")} for u in data]}
    return data


def _get_project_members(project: str = "") -> dict:
    """Get all members who have roles in the project."""
    proj = project or _jira_config()[3]
    # Use assignable search — returns all users assignable to a project
    data = _jira_request("GET", "/user/assignable/search", params={
        "project": proj, "maxResults": 100
    })
    if "error" in data:
        return data
    if isinstance(data, list):
        return {"members": [{"displayName": u.get("displayName", ""), "accountId": u.get("accountId", ""),
                             "email": u.get("emailAddress", "")} for u in data]}
    return data


def _find_stalled_issues(hours_threshold: int = 24, project: str = "") -> dict:
    """Find issues not updated for more than the threshold hours."""
    proj = project or _jira_config()[3]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours_threshold)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M")

    jql = f'project = "{proj}" AND statusCategory != Done AND updated <= "{cutoff_str}" ORDER BY priority DESC, updated ASC'
    result = _run_jql(jql=jql, max_results=50)
    if "error" in result:
        return result

    stalled = result.get("issues", [])
    critical = [i for i in stalled if i.get("priority") in ("Critical", "Highest", "High")]
    missing = [i for i in stalled if not i.get("assignee")]
    blocking = [i for i in stalled if i.get("blockers")]

    return {
        "hours_threshold": hours_threshold,
        "total_stalled": len(stalled),
        "critical_stalled": len(critical),
        "missing_owners": len(missing),
        "blocking_others": len(blocking),
        "issues": stalled,
        "summary": {
            "critical": [i["key"] for i in critical],
            "no_owner": [i["key"] for i in missing],
            "blockers": [i["key"] for i in blocking],
        },
    }


def _get_changes_since(hours: int = 24, project: str = "") -> dict:
    """Get all issues changed in the last N hours."""
    proj = project or _jira_config()[3]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M")

    jql = f'project = "{proj}" AND updated >= "{cutoff_str}" ORDER BY updated DESC'
    result = _run_jql(jql=jql, max_results=50)
    if "error" in result:
        return result

    issues = result.get("issues", [])
    new_issues = []
    resolved = []

    for issue in issues:
        created = issue.get("created", "")
        if created:
            try:
                created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
                if created_dt > cutoff:
                    new_issues.append(issue["key"])
            except (ValueError, TypeError):
                pass
        if issue.get("status_category") == "Done":
            resolved.append(issue["key"])

    return {
        "hours_lookback": hours,
        "total_changed": len(issues),
        "new_issues": new_issues,
        "resolved": resolved,
        "issues": issues,
    }


def _governance_gate(gate_type: str, description: str, ticket_key: str = "") -> dict:
    """Create a human gate request."""
    trust_ledger_log("gate", f"{gate_type}: {description} [{ticket_key}]")
    return {
        "gate_type": gate_type, "description": description,
        "ticket_key": ticket_key, "status": "pending",
        "message": f"⏸️ {gate_type} gate created. Awaiting human decision.",
    }


def _log_decision(decision: str, rationale: str) -> dict:
    """Log a decision to the Trust Ledger."""
    trust_ledger_log("decision", f"{decision}: {rationale}")
    return {"logged": True, "decision": decision}


def _draft_followup_ping(
    ticket_key: str, assignee: str, stalled_hours: int,
    is_critical: bool, message: str,
) -> dict:
    """Draft a follow-up ping for a stalled ticket owner."""
    urgency = "high" if stalled_hours > 72 else "medium" if stalled_hours > 48 else "low"
    trust_ledger_log("tool-call", f"Drafted {urgency} ping for {ticket_key} → {assignee} ({stalled_hours}h)")
    return {
        "ticket_key": ticket_key, "assignee": assignee, "message": message,
        "urgency": urgency, "stalled_hours": stalled_hours,
        "is_critical": is_critical, "status": "drafted — awaiting Review gate",
    }


# ══════════════════════════════════════════════
# ADK FunctionTool wrappers (thin delegates)
# ══════════════════════════════════════════════

def run_jql(jql: str, max_results: int = 50) -> dict:
    """Run any JQL query against Jira. This is your most powerful tool — you can query anything.

    Examples:
        - 'project = ISRDS AND status = "In Progress" ORDER BY priority DESC'
        - 'assignee = "Pegah S." AND statusCategory != Done'
        - 'priority in (Critical, Highest) AND updated < -24h'
        - 'labels = backend AND sprint in openSprints()'
        - 'created >= -7d AND project = ISRDS'
        - 'status changed during (startOfDay(), now())'

    Args:
        jql: Any valid JQL query string.
        max_results: Maximum results to return (default 50).
    """
    return _run_jql(jql, max_results)


def get_issue(issue_key: str) -> dict:
    """Get full details of a Jira issue — status, assignee, changelog, comments, blockers. Use this to deep-dive into a specific ticket.

    Args:
        issue_key: The Jira issue key (e.g., ISRDS-1510).
    """
    return _get_issue(issue_key)


def add_comment(issue_key: str, comment_text: str) -> dict:
    """Post a comment on a Jira ticket. Use this to ask for updates, flag issues, or document PMO decisions.

    Write comments like a real PMO — professional, direct, and helpful:
    - "Hi Todd, this ticket hasn't been updated in 5 days. Can you share a quick status? Is anything blocking progress?"
    - "Flagging: this is on the critical path and needs attention before sprint end."

    Args:
        issue_key: The Jira ticket key (e.g., ISRDS-1510).
        comment_text: The comment text to post. Write like a human PMO.
    """
    return _add_comment(issue_key, comment_text)


def find_stalled_issues(hours_threshold: int = 24, project: str = "") -> dict:
    """Find tickets that haven't been updated for more than N hours. Automatically categorizes them as critical, missing-owner, or blocking.

    Args:
        hours_threshold: Hours of inactivity to consider stalled (default 24).
        project: Jira project key (default: ISRDS).
    """
    return _find_stalled_issues(hours_threshold, project)


def get_changes_since(hours: int = 24, project: str = "") -> dict:
    """See what changed in the last N hours — new tickets, resolved tickets, status changes. Use for the 'since yesterday' section of the Operating Brief.

    Args:
        hours: Look back window in hours (default 24).
        project: Jira project key.
    """
    return _get_changes_since(hours, project)


def search_issues(jql: str = "", project: str = "", max_results: int = 50) -> dict:
    """Quick search for active issues in a project. For complex queries use run_jql instead.

    Args:
        jql: JQL query. If empty, returns all active (not Done) issues.
        project: Jira project key.
        max_results: Max results.
    """
    proj = project or _jira_config()[3]
    if not jql:
        jql = f'project = "{proj}" AND statusCategory != Done ORDER BY updated ASC'
    return _run_jql(jql, max_results)


def transition_issue(issue_key: str, transition_name: str) -> dict:
    """Move a ticket to a different workflow status (e.g., 'To Do' → 'In Progress' → 'Done'). Only use after a governance gate approval.

    Args:
        issue_key: Jira ticket key (e.g., ISRDS-1510).
        transition_name: Target status name (e.g., 'In Progress', 'Done', 'To Do').
    """
    return _transition_issue(issue_key, transition_name)


def find_user(query: str) -> dict:
    """Search for Jira users by name or email. Useful for RACI assignment — finding who should own orphaned tickets.

    Args:
        query: Name or email to search for (e.g., 'Todd', 'saquib@isrdsystems.com').
    """
    return _find_user(query)


def get_team_members(project: str = "") -> dict:
    """Get all team members who can be assigned tickets in the project. Use for RACI analysis and ownership gaps.

    Args:
        project: Jira project key (default: ISRDS).
    """
    return _get_project_members(project)


def governance_gate(gate_type: str, description: str, ticket_key: str = "") -> dict:
    """Create a human approval gate. You MUST use this before sending comments, transitioning tickets, or distributing the Operating Brief.

    Args:
        gate_type: One of 'Review', 'Flag', or 'Escalate'.
        description: What needs human review.
        ticket_key: Related Jira ticket key (optional).
    """
    return _governance_gate(gate_type, description, ticket_key)


def log_decision(decision: str, rationale: str) -> dict:
    """Log every judgment call to the Trust Ledger for audit trail. Use for flagging, escalations, and health assessments.

    Args:
        decision: Short identifier (e.g., 'ticket_stalled', 'escalation_needed', 'build_gap_identified').
        rationale: Your reasoning — why you made this call.
    """
    return _log_decision(decision, rationale)


def draft_followup_ping(
    ticket_key: str, assignee: str, stalled_hours: int,
    is_critical: bool, message: str,
) -> dict:
    """Draft a chase message for a stalled ticket owner. Does NOT send — goes through Review gate first.

    Args:
        ticket_key: Jira ticket key.
        assignee: Person to ping (or 'Unassigned').
        stalled_hours: Hours the ticket has been stalled.
        is_critical: On the critical path?
        message: The chase message. Write like a real PMO — warm but firm.
    """
    return _draft_followup_ping(ticket_key, assignee, stalled_hours, is_critical, message)


# ── Compose the agent ──
root_agent = LlmAgent(
    name="pmo_execution_agent",
    model=MODEL,
    instruction=PROMPT,
    tools=[
        # Core Jira tools
        FunctionTool(run_jql),
        FunctionTool(get_issue),
        FunctionTool(search_issues),
        FunctionTool(add_comment),
        FunctionTool(transition_issue),
        FunctionTool(find_stalled_issues),
        FunctionTool(get_changes_since),
        # People tools
        FunctionTool(find_user),
        FunctionTool(get_team_members),
        # Governance tools
        FunctionTool(governance_gate),
        FunctionTool(log_decision),
        FunctionTool(draft_followup_ping),
    ],
    output_key="response",
)

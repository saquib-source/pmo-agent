"""
Ownership & RACI Agent
Role: surfaces ownership gaps and validates RACI completeness across all active work.
Reads the four verified RACI custom fields (cf_11661/11657/11536/11665).
DECIDE_AND_REPORT — no writes except assign_issue which needs Approve gate.
"""
import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from ..shared import jira_client as jc
from ..shared.governance import trust_ledger_log, governance_check

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "ownership_raci.md"
_PROMPT = _PROMPT_PATH.read_text() if _PROMPT_PATH.exists() else "You are the PMO Ownership & RACI agent."
from ..shared.config_registry import adk_model
MODEL = adk_model(os.environ.get("AGENT_MODEL", "gemini-2.5-flash"))


# ── Tools ────────────────────────────────────────────────────────────────────

def get_raci(issue_key: str) -> dict:
    """Read the RACI custom fields for a specific ticket.
    Returns: Accountable (cf_11661), Responsible (cf_11657), Consulted (cf_11536), Informed (cf_11665),
    plus division/department/EASS rating/product_level/feature_set/phase/budget.

    Args:
        issue_key: Jira ticket key (e.g. ISRDS-1573).
    """
    issue = jc.get_issue_detail(issue_key)
    if "error" in issue:
        return issue
    raci = issue.get("raci", {}) or {}
    acc = (raci.get("accountable") or {}).get("name") or "—"
    res = (raci.get("responsible") or {}).get("name") or "—"
    # Telemetry: record the RACI lookup so the dashboard reflects real activity and
    # we can confirm Follow-up consults ownership before drafting.
    trust_ledger_log(
        "raci",
        f"Resolved RACI for {issue_key}: Accountable={acc}, Responsible={res}",
        agent_id="pmo_raci",
    )
    return {
        "key":   issue_key,
        "raci":  raci,
        "owner": issue.get("assignee"),
        "url":   issue.get("url", ""),
    }


def audit_raci_gaps(project: str = "", max_results: int = 100) -> dict:
    """Scan all active tickets in a project for RACI gaps: missing Accountable, Responsible, or no assignee.

    Args:
        project: Project key (default ISRDS). Use 'ALL' for all 8 projects.
        max_results: How many active tickets to inspect (default 100).
    """
    if project.upper() == "ALL":
        proj_jql = " OR ".join(f'project = "{p}"' for p in jc.ALL_PROJECTS)
        jql = f'({proj_jql}) AND statusCategory != Done ORDER BY priority DESC'
    else:
        proj = project or "ISRDS"
        jql = f'project = "{proj}" AND statusCategory != Done ORDER BY priority DESC'

    result = jc.run_jql(jql, max_results,
                         extra_fields=list(jc.RACI_FIELDS.values()))
    if "error" in result:
        return result

    no_assignee   = []
    no_accountable = []
    no_responsible = []

    for issue in result["issues"]:
        key = issue["key"]
        if not issue.get("assignee"):
            no_assignee.append(key)
        raci = issue.get("raci") or {}
        if not raci.get("accountable"):
            no_accountable.append(key)
        if not raci.get("responsible"):
            no_responsible.append(key)

    gap_count = len(set(no_assignee + no_accountable + no_responsible))
    # Telemetry: record the audit so the dashboard shows RACI activity.
    trust_ledger_log(
        "raci",
        f"RACI gap audit: {result['total']} scanned, {gap_count} with gaps "
        f"(no-assignee={len(no_assignee)}, no-accountable={len(no_accountable)}, "
        f"no-responsible={len(no_responsible)})",
        agent_id="pmo_raci",
    )
    return {
        "total_scanned":    result["total"],
        "no_assignee":      no_assignee,
        "no_accountable":   no_accountable,
        "no_responsible":   no_responsible,
        "gap_count":        gap_count,
    }


def find_user(query: str) -> dict:
    """Search for a Jira user by name or email. Useful for identifying who should own orphaned tickets.

    Args:
        query: Name or email fragment (e.g. 'Todd', 'saquib@isrdsystems.com').
    """
    return jc.find_user(query)


def get_team_members(project: str = "") -> dict:
    """Get all users assignable in a project. Use for RACI analysis and gap-filling suggestions.

    Args:
        project: Jira project key (default ISRDS).
    """
    return jc.get_project_members(project)


def assign_ticket(issue_key: str, account_id: str) -> dict:
    """Assign a ticket to a team member. Requires Approve gate — only call after orchestrator approval.

    Args:
        issue_key: Jira ticket key.
        account_id: Jira accountId of the new assignee (use find_user to look this up).
    """
    check = governance_check("jira-assign", is_irreversible=False)
    if not check["allowed"]:
        return {
            "blocked": True,
            "gate":    check.get("gate", "Approve"),
            "reason":  check["reason"],
        }
    return jc.assign_issue(issue_key, account_id, agent_id="pmo_raci")


# ── Agent ────────────────────────────────────────────────────────────────────

ownership_raci_agent = LlmAgent(
    name="ownership_raci_agent",
    model=MODEL,
    instruction=_PROMPT,
    tools=[
        FunctionTool(get_raci),
        FunctionTool(audit_raci_gaps),
        FunctionTool(find_user),
        FunctionTool(get_team_members),
        FunctionTool(assign_ticket),
    ],
    output_key="response",
)

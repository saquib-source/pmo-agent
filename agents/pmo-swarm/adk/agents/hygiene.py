"""
Hygiene Agent — REPORT-ONLY.
Role: scans Jira ticket hygiene (assignee, time estimate, due date) and surfaces
findings to the orchestrator for the Operating Brief. It NEVER messages ticket
owners — housekeeping comments ("wrong type", "missing Epic link", "add an
estimate") are disabled by owner decision, 2026-07-14. Issue type and Epic
linkage are not policed at all.

Inter-agent wiring:
  ← called by: orchestrator
  → calls:     nobody (no follow_up handoff — findings are internal only)
"""
import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool

from ..shared import jira_client as jc
from ..shared.governance import trust_ledger_log

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "hygiene.md"
_PROMPT = _PROMPT_PATH.read_text() if _PROMPT_PATH.exists() else "You are the PMO Hygiene agent."
from ..shared.config_registry import adk_model
MODEL = adk_model(os.environ.get("AGENT_MODEL", "gemini-2.5-flash"))


# ── Tools ────────────────────────────────────────────────────────────────────

def check_issue_hygiene(issue_key: str) -> dict:
    """Check a single ticket against PMO hygiene standards.

    Checks (internal reporting only — never messaged to ticket owners):
      - Has an assignee
      - Has time estimate (originalEstimate)
      - Has a due date

    Issue type and Epic link are intentionally NOT checked — do not report
    or message about them.

    Args:
        issue_key: Jira ticket key (e.g. ISRDS-1510).
    """
    data = jc.jira_request("GET", f"/issue/{issue_key}", params={
        "fields": "summary,issuetype,assignee,timeoriginalestimate,duedate,project"
    })
    if "error" in data:
        return data

    fields = data.get("fields", {})
    violations = []

    issue_type = (fields.get("issuetype") or {}).get("name", "")

    if not fields.get("assignee"):
        violations.append("No assignee")

    if not fields.get("timeoriginalestimate"):
        violations.append("No original time estimate")

    if not fields.get("duedate"):
        violations.append("No due date")

    result = {
        "key":        issue_key,
        "summary":    fields.get("summary", ""),
        "project":    (fields.get("project") or {}).get("key", ""),
        "issue_type": issue_type,
        "violations": violations,
        "clean":      len(violations) == 0,
    }

    if violations:
        trust_ledger_log(
            "hygiene-violation",
            f"{issue_key}: {'; '.join(violations)}",
            agent_id="pmo_hygiene",
        )

    return result


def scan_hygiene(project: str = "", max_results: int = 50) -> dict:
    """Scan all active tickets in a project for hygiene violations.

    Args:
        project: Jira project key (default ISRDS). Use 'ALL' for all 8 projects.
        max_results: Tickets to inspect (default 50).
    """
    # Only police hygiene on work that is genuinely in flight: active sprint AND
    # In Progress. Excludes backlog (no active sprint), Done, and To Do — matching
    # the follow-up eligibility policy, so we never draft notes we won't post.
    elig = 'sprint in openSprints() AND statusCategory = "In Progress"'
    if project.upper() == "ALL":
        proj_jql = " OR ".join(f'project = "{p}"' for p in jc.ALL_PROJECTS)
        jql = f'({proj_jql}) AND {elig} ORDER BY priority DESC'
    else:
        proj = project or "ISRDS"
        jql = f'project = "{proj}" AND {elig} ORDER BY priority DESC'

    result = jc.run_jql(jql, max_results, extra_fields=[
        "timeoriginalestimate", "duedate",
    ])
    if "error" in result:
        return result

    violations_by_type = {
        "no_assignee":       [],
        "no_estimate":       [],
        "no_due_date":       [],
    }

    for issue in result["issues"]:
        key = issue["key"]
        if not issue.get("assignee"):
            violations_by_type["no_assignee"].append(key)

    return {
        "total_scanned": result["total"],
        "violations":    violations_by_type,
        "total_violations": sum(len(v) for v in violations_by_type.values()),
    }


# ── Agent ────────────────────────────────────────────────────────────────────

hygiene_agent = LlmAgent(
    name="hygiene_agent",
    model=MODEL,
    instruction=_PROMPT,
    tools=[
        FunctionTool(check_issue_hygiene),
        FunctionTool(scan_hygiene),
        # REPORT-ONLY: no follow_up_agent here. Hygiene findings must never turn
        # into comments to ticket owners (owner decision, 2026-07-14).
    ],
    output_key="response",
)

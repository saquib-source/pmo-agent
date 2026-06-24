"""
Execution Tracking Agent
Role: board observer. Scans Jira across all projects, identifies stalled/blocked work,
surfaces what changed. DECIDE_AND_REPORT for reads — no direct Jira writes.

Inter-agent wiring:
  ← called by: orchestrator (entry point for every brief cycle)
  → calls:     follow_up_agent when stalled tickets are found (>= threshold hours, Critical/High)
               follow_up will in turn call ownership_raci to get RACI before drafting
"""
import os
from pathlib import Path

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool

from ..shared import jira_client as jc
from .follow_up import follow_up_agent

_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "execution_tracking.md"
_PROMPT = _PROMPT_PATH.read_text() if _PROMPT_PATH.exists() else "You are the PMO Execution Tracking agent."
MODEL = os.environ.get("AGENT_MODEL", "gemini-2.5-flash")


# ── Tools ────────────────────────────────────────────────────────────────────

def run_jql(jql: str, max_results: int = 50) -> dict:
    """Run any JQL query across Jira. Your most powerful read tool.

    Examples:
        'project in (ISRDS, ASHS) AND statusCategory != Done ORDER BY priority DESC'
        'assignee = "Pegah S." AND statusCategory != Done'
        'priority in (Critical, Highest) AND updated < -24h'
        'status changed during (startOfDay(), now())'

    Args:
        jql: Valid JQL string. Use project in (...) to span multiple projects.
        max_results: Cap on results (default 50).
    """
    return jc.run_jql(jql, max_results)


def get_issue(issue_key: str) -> dict:
    """Get the full detail of one ticket: status, assignee, RACI, changelog, all comments, blockers.

    Args:
        issue_key: e.g. ISRDS-1510, ASHS-234
    """
    return jc.get_issue_detail(issue_key)


def search_active(project: str = "", max_results: int = 50) -> dict:
    """Quick scan of all active (not Done) tickets in a project.

    Args:
        project: Jira project key. Omit or pass 'ALL' to scan all configured projects.
        max_results: Cap on results.
    """
    if not project or project.upper() == "ALL":
        proj_jql = " OR ".join(f'project = "{p}"' for p in jc.ALL_PROJECTS)
        jql = f'({proj_jql}) AND statusCategory != Done ORDER BY priority DESC, updated ASC'
    else:
        jql = f'project = "{project}" AND statusCategory != Done ORDER BY priority DESC, updated ASC'
    return jc.run_jql(jql, max_results)


def find_stalled_issues(hours_threshold: int = 24, project: str = "") -> dict:
    """Find tickets with no activity for more than N hours. Categorises by criticality.

    Args:
        hours_threshold: Inactivity threshold in hours (default 24).
        project: Project key, or omit/'ALL' to scan all configured projects.
    """
    projects = None if (not project or project.upper() == "ALL") else [project]
    return jc.find_stalled(hours_threshold, projects)


def get_changes_since(hours: int = 24, project: str = "") -> dict:
    """What moved in the last N hours? New tickets, resolved, status transitions.

    Args:
        hours: Look-back window (default 24).
        project: Project key, or 'ALL'.
    """
    projects = jc.ALL_PROJECTS if project.upper() == "ALL" else ([project] if project else None)
    return jc.get_changes_since(hours, projects)


# ── Agent ────────────────────────────────────────────────────────────────────

execution_tracking_agent = LlmAgent(
    name="execution_tracking_agent",
    model=MODEL,
    instruction=_PROMPT,
    tools=[
        FunctionTool(run_jql),
        FunctionTool(get_issue),
        FunctionTool(search_active),
        FunctionTool(find_stalled_issues),
        FunctionTool(get_changes_since),
        # Hand off stalled/blocked tickets directly to Follow-up for chase drafting.
        # Follow-up will call ownership_raci internally to resolve RACI before posting.
        AgentTool(follow_up_agent),
    ],
    output_key="response",
)

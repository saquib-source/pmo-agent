"""
Layer 3 — Tool Registry
Central catalogue of every tool in the PMO swarm.
Records ownership, authorization, and governance requirements per tool.

This is a metadata layer — it does not change how ADK FunctionTools are wired.
It enables:
  - Audit: which agent called which tool, with what governance gate
  - Authorization checks before sensitive tool calls
  - Portable artifact: tool_registry.yaml (emitted by emit_artifact())

Usage:
    from .tool_registry import registry, emit_artifact
    registry.register(...)
    emit_artifact()
"""
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Gate types matching governance.py authority gradient
GATE_REVIEW  = "Review"
GATE_APPROVE = "Approve"
GATE_ESCALATE = "Escalate"


@dataclass
class ToolEntry:
    name: str
    owner_agent: str
    description: str
    requires_gate: bool = False
    gate_type: Optional[str] = None
    is_irreversible: bool = False
    authorized_agents: list = field(default_factory=list)


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolEntry] = {}

    def register(
        self,
        name: str,
        owner_agent: str,
        description: str,
        requires_gate: bool = False,
        gate_type: Optional[str] = None,
        is_irreversible: bool = False,
        authorized_agents: Optional[list] = None,
    ) -> None:
        self._tools[name] = ToolEntry(
            name=name,
            owner_agent=owner_agent,
            description=description,
            requires_gate=requires_gate,
            gate_type=gate_type,
            is_irreversible=is_irreversible,
            authorized_agents=authorized_agents or [owner_agent, "pmo_orchestrator"],
        )

    def is_authorized(self, tool_name: str, agent_id: str) -> bool:
        entry = self._tools.get(tool_name)
        if entry is None:
            return True   # unregistered tools are not blocked (backward compat)
        return agent_id in entry.authorized_agents or entry.owner_agent == agent_id

    def requires_gate(self, tool_name: str) -> Optional[str]:
        """Return the gate_type required before calling this tool, or None."""
        entry = self._tools.get(tool_name)
        return entry.gate_type if entry and entry.requires_gate else None

    def get(self, name: str) -> Optional[ToolEntry]:
        return self._tools.get(name)

    def all_tools(self) -> list[ToolEntry]:
        return list(self._tools.values())

    def emit_artifact(self, output_path: Optional[Path] = None) -> Path:
        """Write tool_registry.yaml — portable artifact #3 of 6."""
        if output_path is None:
            output_path = Path(__file__).parent.parent / "tool_registry.yaml"

        import yaml
        try:
            from .config_registry import get_tenant_id
            tenant = get_tenant_id()
        except Exception:
            import os
            tenant = os.environ.get("TENANT_ID", "ashs")

        entries = [
            {
                "name":               t.name,
                "owner_agent":        t.owner_agent,
                "description":        t.description,
                "requires_gate":      t.requires_gate,
                "gate_type":          t.gate_type,
                "is_irreversible":    t.is_irreversible,
                "authorized_agents":  t.authorized_agents,
            }
            for t in sorted(self._tools.values(), key=lambda x: (x.owner_agent, x.name))
        ]

        artifact = {
            "version": "1.0",
            "swarm":   "pmo-swarm",
            "tenant":  tenant,
            "tools":   entries,
        }

        output_path.write_text(yaml.dump(artifact, default_flow_style=False, sort_keys=False))
        log.info(f"Tool registry artifact written → {output_path} ({len(entries)} tools)")
        return output_path


# Singleton — imported everywhere
registry = ToolRegistry()


def _seed_registry() -> None:
    """Register all known PMO swarm tools. Called once at startup."""

    # ── Orchestrator tools ───────────────────────────────────────────────────
    registry.register(
        "governance_gate", "pmo_orchestrator",
        "Create a human approval checkpoint before any Jira write.",
        requires_gate=False,
    )
    registry.register(
        "log_decision", "pmo_orchestrator",
        "Record a significant judgment call to the Trust Ledger.",
    )
    registry.register(
        "read_ledger", "pmo_orchestrator",
        "Read recent Trust Ledger entries for audit.",
    )

    # ── Execution Tracking tools ─────────────────────────────────────────────
    registry.register(
        "run_jql", "execution_tracking_agent",
        "Run a JQL query against Jira and return matching issues.",
        authorized_agents=["execution_tracking_agent", "hygiene_agent", "pmo_orchestrator"],
    )
    registry.register(
        "get_issue", "execution_tracking_agent",
        "Fetch full details for a single Jira issue.",
        authorized_agents=["execution_tracking_agent", "follow_up_agent",
                           "ownership_raci_agent", "hygiene_agent", "pmo_orchestrator"],
    )
    registry.register(
        "search_active", "execution_tracking_agent",
        "Find all in-progress tickets across configured projects.",
    )
    registry.register(
        "find_stalled_issues", "execution_tracking_agent",
        "Identify tickets with no activity beyond the stall threshold.",
    )
    registry.register(
        "get_changes_since", "execution_tracking_agent",
        "Return all Jira changes since a given timestamp.",
    )

    # ── Follow-up tools ──────────────────────────────────────────────────────
    registry.register(
        "draft_followup_ping", "follow_up_agent",
        "Draft a follow-up message for a stalled ticket.",
    )
    registry.register(
        "post_comment", "follow_up_agent",
        "Post an approved comment to a Jira ticket.",
        requires_gate=True, gate_type=GATE_REVIEW, is_irreversible=False,
    )
    registry.register(
        "request_transition", "follow_up_agent",
        "Request a Jira status transition (requires Approve gate).",
        requires_gate=True, gate_type=GATE_APPROVE, is_irreversible=True,
    )
    registry.register(
        "escalate", "follow_up_agent",
        "Flag a ticket for leadership escalation.",
        requires_gate=True, gate_type=GATE_ESCALATE,
    )

    # ── Ownership / RACI tools ───────────────────────────────────────────────
    registry.register(
        "get_raci", "ownership_raci_agent",
        "Read RACI fields for a Jira issue.",
        authorized_agents=["ownership_raci_agent", "follow_up_agent",
                           "feature_completeness_agent", "pmo_orchestrator"],
    )
    registry.register(
        "audit_raci_gaps", "ownership_raci_agent",
        "Find tickets missing accountable or responsible owners.",
    )
    registry.register(
        "find_user", "ownership_raci_agent",
        "Resolve a display name to a Jira account ID.",
    )
    registry.register(
        "get_team_members", "ownership_raci_agent",
        "List members of a Jira project.",
    )
    registry.register(
        "assign_ticket", "ownership_raci_agent",
        "Assign a Jira ticket to a specific user.",
        requires_gate=True, gate_type=GATE_APPROVE, is_irreversible=False,
    )

    # ── Feature Completeness tools ───────────────────────────────────────────
    registry.register(
        "audit_features", "feature_completeness_agent",
        "Audit Firestore navigation catalog — built vs unbuilt features.",
    )
    registry.register(
        "get_division_detail", "feature_completeness_agent",
        "Drill into unbuilt features within a specific division.",
    )

    # ── Hygiene tools ────────────────────────────────────────────────────────
    registry.register(
        "check_issue_hygiene", "hygiene_agent",
        "Check a single issue for hygiene violations.",
        authorized_agents=["hygiene_agent", "pmo_orchestrator"],
    )
    registry.register(
        "scan_hygiene", "hygiene_agent",
        "Scan all issues in a project for hygiene violations.",
    )


# Auto-seed on import
_seed_registry()

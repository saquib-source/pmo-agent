"""
Install-or-Furnish Decision
BISD swarm: install-or-furnish-decision
STATUS: STUB — implement during build Phase 3.
"""
# from google.adk.agents import LlmAgent
# from ..shared.config_registry import resolve_engine
# from ..shared.governance import governance_check, log_decision
# from ..shared.observability import emit_metric

IS_ACTIVE = False          # overridden at runtime by Config Registry is_active flag


def build_agent():
    """Return a configured LlmAgent. Raises RuntimeError if is_active is False."""
    if not IS_ACTIVE:
        raise RuntimeError(
            "install_or_furnish_decision_agent is DORMANT. Set is_active=true in Config Registry to activate."
        )
    # engine = resolve_engine("basco", "install-or-furnish-decision")
    # return LlmAgent(
    #     name="install_or_furnish_decision_agent",
    #     model=engine["model"],
    #     instruction=open("prompts/agent.md").read(),
    # )
    raise NotImplementedError("STUB — implement during build Phase 3")


def run_stub(payload: dict) -> dict:
    """No-op stub. Logs the call and returns a NOOP signal."""
    result = {
        "agent": "install_or_furnish_decision_agent",
        "status": "NOOP",
        "reason": "STUB — agent is_active=false",
        "payload_received": bool(payload),
    }
    # log_decision(agent_id="install-or-furnish-decision", action="stub_noop", outcome="noop")
    return result

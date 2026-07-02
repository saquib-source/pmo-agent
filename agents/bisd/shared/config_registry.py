"""
BISD shared config registry stub.
Mirrors agents/pmo-swarm/adk/shared/config_registry.py pattern.
"""


def resolve_engine(tenant_id: str, agent_id: str) -> dict:
    """
    Resolve the runtime engine for a BISD agent.
    STUB — reads from Firestore system_config/bisd_agent_config in production.
    Returns: {model, tools, guardrails, is_active}
    """
    return {
        "model": None,
        "tools": [],
        "guardrails": None,
        "is_active": False,     # ALL agents start dormant
    }

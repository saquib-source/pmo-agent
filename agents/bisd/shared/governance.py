"""
BISD shared governance stub.
Mirrors agents/pmo-swarm/adk/shared/governance.py pattern.
"""


def governance_check(agent_id: str, action: str, rules: list) -> dict:
    """
    Check whether an action is allowed, gated, or denied.
    STUB — loads governance-rules.yaml per agent in production.
    """
    return {"decision": "deny", "reason": "STUB — governance not yet implemented"}


def log_decision(agent_id: str, action: str, outcome: str, gate_type: str = None) -> None:
    """
    Append a decision record to the Trust Ledger (BigQuery isrds_bisd.trust_events).
    STUB.
    """
    pass

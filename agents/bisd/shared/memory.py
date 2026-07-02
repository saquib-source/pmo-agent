"""
BISD shared memory stub.
Mirrors agents/pmo-swarm/adk/shared/memory.py pattern.
Backend: Vertex AI Agent Engine (VertexAiSessionService)
"""


def get_memory(agent_id: str, namespace: str, key: str):
    """STUB — retrieve a memory value from agent session."""
    return None


def set_memory(agent_id: str, namespace: str, key: str, value) -> None:
    """STUB — store a memory value in agent session."""
    pass

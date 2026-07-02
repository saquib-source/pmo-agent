"""
BISD shared observability stub.
Mirrors agents/pmo-swarm/adk/shared/observability.py pattern.
"""


def emit_metric(agent_id: str, metric_name: str, value: float, labels: dict = None) -> None:
    """
    Emit a metric to Cloud Monitoring.
    Namespace: custom.googleapis.com/isrds/bisd/{agent_id}/{metric_name}
    STUB.
    """
    pass


def emit_log(agent_id: str, event_type: str, payload: dict) -> None:
    """
    Emit a structured log to Cloud Logging.
    logName: isrds/bisd/{agent_id}
    STUB.
    """
    pass

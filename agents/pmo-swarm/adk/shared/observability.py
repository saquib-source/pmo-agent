"""
Layer 8 — Observability (GCP)
Structured logs  → Cloud Logging  (logName: isrds/pmo-swarm)
Custom metrics   → Cloud Monitoring (custom.googleapis.com/isrds/pmo-swarm/*)
Agent run traces → Cloud Trace (via Cloud Logging correlation IDs)

Falls back to local stdlib logging when GCP is unavailable (local dev, CI).
The local trust-ledger.jsonl is kept as an additional backup — it is NOT replaced.
"""
import os
import time
import logging
import contextlib
import uuid
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

_gcp_log_client = None
_gcp_logger = None
_metrics_client = None

_PROJECT: str = ""
_SWARM = "pmo-swarm"
_TENANT: str = ""
_ENABLED: bool | None = None   # None = not yet resolved


def _resolve_config() -> tuple[str, str, bool]:
    """Lazy-load config without circular import at module level."""
    global _PROJECT, _TENANT, _ENABLED
    if _ENABLED is None:
        try:
            from .config_registry import get_gcp_project, get_tenant_id, is_observability_enabled
            _PROJECT = get_gcp_project()
            _TENANT  = get_tenant_id()
            _ENABLED = is_observability_enabled()
        except Exception:
            _PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "isr-division-systems-488723")
            _TENANT  = os.environ.get("TENANT_ID", "ashs")
            _ENABLED = os.environ.get("OBSERVABILITY_ENABLED", "true").lower() == "true"
    return _PROJECT, _TENANT, _ENABLED


def _get_gcp_logger():
    global _gcp_log_client, _gcp_logger
    if _gcp_logger is None:
        project, _, enabled = _resolve_config()
        if enabled:
            try:
                from google.cloud import logging as gcp_logging
                _gcp_log_client = gcp_logging.Client(project=project)
                _gcp_logger = _gcp_log_client.logger(f"isrds/{_SWARM}")
            except Exception as e:
                log.warning(f"Observability: Cloud Logging unavailable ({e})")
                _gcp_logger = False
    return _gcp_logger if _gcp_logger else None


def _get_metrics_client():
    global _metrics_client
    if _metrics_client is None:
        _, _, enabled = _resolve_config()
        if enabled:
            try:
                from google.cloud import monitoring_v3
                _metrics_client = monitoring_v3.MetricServiceClient()
            except Exception as e:
                log.warning(f"Observability: Cloud Monitoring unavailable ({e})")
                _metrics_client = False
    return _metrics_client if _metrics_client else None


def log_event(
    event_type: str,
    detail: str,
    agent_id: str = "pmo_swarm",
    severity: str = "INFO",
    extra: Optional[dict] = None,
    trace_id: Optional[str] = None,
) -> None:
    """Write a structured log entry to Cloud Logging.

    Always also writes to local stdlib logger as a backup.
    """
    project, tenant, _ = _resolve_config()
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "swarm":      _SWARM,
        "tenant":     tenant,
        "agent_id":   agent_id,
        "event_type": event_type,
        "detail":     str(detail)[:1000],
        **(extra or {}),
    }
    if trace_id:
        entry["trace_id"] = trace_id

    gcp = _get_gcp_logger()
    if gcp:
        try:
            kwargs = {}
            if trace_id and project:
                kwargs["trace"] = f"projects/{project}/traces/{trace_id}"
            gcp.log_struct(entry, severity=severity, **kwargs)
        except Exception as e:
            log.warning(f"Observability: Cloud Logging write failed ({e})")

    getattr(log, severity.lower(), log.info)(
        f"[{agent_id}] {event_type}: {str(detail)[:200]}"
    )


def record_metric(
    metric_name: str,
    value: float,
    labels: Optional[dict] = None,
) -> None:
    """Increment or set a custom Cloud Monitoring metric."""
    client = _get_metrics_client()
    if not client:
        log.debug(f"metric {metric_name}={value} (Cloud Monitoring not available)")
        return
    try:
        from google.cloud import monitoring_v3
        from google.protobuf.timestamp_pb2 import Timestamp

        project, tenant, _ = _resolve_config()
        series = monitoring_v3.TimeSeries()
        series.metric.type = f"custom.googleapis.com/isrds/{_SWARM}/{metric_name}"
        series.resource.type = "global"
        series.metric.labels["tenant"] = tenant
        for k, v in (labels or {}).items():
            series.metric.labels[k] = str(v)

        now = time.time()
        ts = Timestamp(seconds=int(now), nanos=int((now % 1) * 1e9))
        point = monitoring_v3.Point()
        point.interval.end_time = ts
        point.value.double_value = float(value)
        series.points.append(point)

        client.create_time_series(
            name=f"projects/{project}",
            time_series=[series],
        )
    except Exception as e:
        log.warning(f"Observability: Monitoring write failed for {metric_name} ({e})")


@contextlib.contextmanager
def trace_agent_run(agent_id: str, extra: Optional[dict] = None):
    """Context manager — records duration + emits run_start / run_complete log entries."""
    trace_id = uuid.uuid4().hex
    start = time.perf_counter()
    log_event("agent_run_start", f"{agent_id} started", agent_id=agent_id,
              extra=extra, trace_id=trace_id)
    try:
        yield trace_id
    except Exception as exc:
        log_event("agent_run_error", str(exc), agent_id=agent_id,
                  severity="ERROR", trace_id=trace_id)
        record_metric("agent_errors_total", 1.0, labels={"agent_id": agent_id})
        raise
    finally:
        duration_ms = (time.perf_counter() - start) * 1000
        log_event(
            "agent_run_complete",
            f"{agent_id} finished in {duration_ms:.0f}ms",
            agent_id=agent_id,
            extra={"duration_ms": duration_ms},
            trace_id=trace_id,
        )
        record_metric("agent_run_duration_ms", duration_ms, labels={"agent_id": agent_id})

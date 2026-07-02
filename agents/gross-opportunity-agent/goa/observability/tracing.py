"""
Tracing — OpenTelemetry spans exported to Cloud Trace.
Every function call, model call, and source pull is a span.
Section 14 of the build spec.
"""

from __future__ import annotations
import logging
from contextlib import contextmanager
from typing import Any

log = logging.getLogger(__name__)

try:
    from opentelemetry import trace  # type: ignore
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore
    from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore
    from opentelemetry.exporter.cloud_trace import CloudTraceSpanExporter  # type: ignore

    _tracer_provider = TracerProvider()
    _tracer_provider.add_span_processor(BatchSpanProcessor(CloudTraceSpanExporter()))
    trace.set_tracer_provider(_tracer_provider)
    _tracer = trace.get_tracer("goa")
    _OTEL_AVAILABLE = True
except ImportError:
    _tracer = None
    _OTEL_AVAILABLE = False
    log.info("OpenTelemetry not installed — tracing disabled")


@contextmanager
def span(name: str, attributes: dict | None = None):
    """Context manager for a single trace span. No-op if OTEL not available."""
    if not _OTEL_AVAILABLE or _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(name) as s:
        if attributes:
            for k, v in attributes.items():
                s.set_attribute(k, str(v))
        yield s

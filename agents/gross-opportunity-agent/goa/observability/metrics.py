"""
Metrics — per-run and per-connector counters for the operational dashboard.
Section 14 of the build spec. Gap: cost-per-call tagging once ADK model calls are wired.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)


@dataclass
class RunMetrics:
    source_id: str
    mode: str
    started_at: datetime = field(default_factory=datetime.utcnow)
    records_pulled: int = 0
    records_normalized: int = 0
    records_merged: int = 0
    records_kept: int = 0
    records_dropped: int = 0
    records_forked: int = 0
    model_calls: int = 0
    model_cost_usd: float = 0.0

    def log_summary(self) -> None:
        log.info(
            "Run summary source=%s mode=%s pulled=%d normalized=%d merged=%d kept=%d dropped=%d forked=%d "
            "model_calls=%d model_cost_usd=%.4f elapsed_s=%.1f",
            self.source_id, self.mode,
            self.records_pulled, self.records_normalized, self.records_merged,
            self.records_kept, self.records_dropped, self.records_forked,
            self.model_calls, self.model_cost_usd,
            (datetime.utcnow() - self.started_at).total_seconds(),
        )

    def emit_to_cloud_monitoring(self) -> None:
        """Push custom metrics to Cloud Monitoring.
        Gap: wire to google-cloud-monitoring once metric descriptors are created.
        """
        log.debug("emit_to_cloud_monitoring: not yet wired (Gap)")


def record_model_call(metrics: RunMetrics, role: str, cost_usd: float = 0.0) -> None:
    """Increment model call counter. Tag by role so spend is attributable."""
    metrics.model_calls += 1
    metrics.model_cost_usd += cost_usd
    log.debug("Model call: role=%s cost=%.4f", role, cost_usd)

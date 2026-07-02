"""
GOA Workflow Definition — ADK-compatible orchestration.
Defines the two run modes (backfill, delta) and the six-function pipeline per record.
Section 6.1 and Section 7 of the build spec.

This file is the ADK entry point. It wraps orchestrator.run_source with ADK lifecycle hooks
and exposes the scheduled job endpoints.
"""

from __future__ import annotations
import asyncio
import logging
import os

log = logging.getLogger(__name__)


# ── ADK Agent definition ──────────────────────────────────────────────────────

def build_adk_agent():
    """Construct the ADK Agent object for deployment to Vertex AI Agent Engine.
    Gap: ADK API shape — confirm with ADK documentation when wiring to runtime.
    """
    try:
        import google.adk  # type: ignore
        # Gap: wire to actual ADK API. Placeholder structure below.
        agent = google.adk.Agent(
            name="gross-opportunity-agent",
            model=None,       # Orchestrator uses no model — functions resolve their own via Config Registry
            instruction=open(
                os.path.join(os.path.dirname(__file__), "prompt.md")
            ).read(),
        )
        return agent
    except ImportError:
        log.warning("google.adk not installed — ADK agent construction skipped")
        return None


# ── Workflow entry points ─────────────────────────────────────────────────────

async def run_backfill(source_id: str) -> None:
    """Heavy first pass for a newly enabled source. Run once per source manually."""
    from goa.orchestrator import run_source
    await run_source(source_id, "backfill")


async def run_delta(source_id: str) -> None:
    """Scheduled delta run — detects new and changed records since the watermark."""
    from goa.orchestrator import run_source
    await run_source(source_id, "delta")


async def run_expiration_sweep() -> None:
    """Daily sweep — closes active opportunities past their bid_date."""
    from jobs.expiration_sweep import run
    await run()


async def run_liveness_check() -> None:
    """Run the liveness watchdog across all enabled sources."""
    from goa.watchdog.liveness import check_all_sources
    await check_all_sources()


async def run_scout() -> None:
    """Weekly source discovery scout (behind APPROVE gate)."""
    from goa.scout.scout import run_scout as _run
    await _run()


async def health() -> dict:
    """Critical-state health check. Called by the monitoring system."""
    from goa.observability.critical_state import health_check
    return await health_check()


# ── HTTP handler shim for Cloud Run / Agent Runtime ───────────────────────────

async def handle_request(event: dict) -> dict:
    """Route incoming Cloud Scheduler or Cloud Run HTTP events to the right workflow."""
    job = event.get("job") or event.get("path", "").strip("/")
    source_id = event.get("source") or event.get("source_id")

    if job == "delta" and source_id:
        await run_delta(source_id)
        return {"status": "ok", "job": "delta", "source_id": source_id}

    if job == "backfill" and source_id:
        await run_backfill(source_id)
        return {"status": "ok", "job": "backfill", "source_id": source_id}

    if job == "expiration_sweep":
        await run_expiration_sweep()
        return {"status": "ok", "job": "expiration_sweep"}

    if job == "liveness":
        await run_liveness_check()
        return {"status": "ok", "job": "liveness"}

    if job == "scout":
        await run_scout()
        return {"status": "ok", "job": "scout"}

    if job == "health":
        result = await health()
        return {"status": "ok", "job": "health", "result": result}

    return {"status": "error", "message": f"Unknown job: {job}"}

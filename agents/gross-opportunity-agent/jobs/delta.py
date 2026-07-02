"""
Delta job — detect and process only new and changed records since the last run.
Triggered by Cloud Scheduler on a per-source cadence (cadence_cron in source_registry).
Step 6 / Section 7.2 of the build spec.

Usage (invoked by Cloud Scheduler HTTP target):
  POST /run-delta?source=sam_gov

Or directly:
  python -m jobs.delta --source sam_gov
"""

import argparse
import asyncio
import logging

log = logging.getLogger(__name__)


async def run(source_id: str) -> None:
    from goa.orchestrator import run_source
    log.info("Delta: starting source=%s mode=delta", source_id)
    await run_source(source_id, "delta")
    log.info("Delta: completed source=%s", source_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="GOA delta job")
    parser.add_argument("--source", required=True, help="source_id from source_registry")
    args = parser.parse_args()
    asyncio.run(run(args.source))

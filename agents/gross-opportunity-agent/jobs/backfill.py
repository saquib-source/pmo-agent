"""
Backfill job — one heavy pass through a source when it is first enabled.
Run once manually per source after the source is approved and credentials are in Secret Manager.
Step 6 / Section 7.1 of the build spec.

Usage:
  python -m jobs.backfill --source sam_gov
"""

import argparse
import asyncio
import logging

log = logging.getLogger(__name__)


async def run(source_id: str) -> None:
    from goa.orchestrator import run_source
    log.info("Backfill: starting source=%s mode=backfill", source_id)
    await run_source(source_id, "backfill")
    log.info("Backfill: completed source=%s", source_id)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="GOA backfill job")
    parser.add_argument("--source", required=True, help="source_id from source_registry")
    args = parser.parse_args()
    asyncio.run(run(args.source))

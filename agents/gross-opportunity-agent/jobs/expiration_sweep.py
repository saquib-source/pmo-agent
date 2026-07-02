"""
Expiration sweep — daily pass that closes any active opportunity past its bid_date.
Never deletes; sets status = 'closed' with closed_reason = 'expired'.
Step 6 / Section 7.3 of the build spec. Cadence: nightly (Calibrated).

Usage:
  python -m jobs.expiration_sweep
"""

import asyncio
import logging
from datetime import date

log = logging.getLogger(__name__)


async def run() -> None:
    from goa.stores import cloudsql

    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        result = await conn.execute(
            """
            UPDATE opportunity
            SET status = 'closed', closed_reason = 'expired', last_changed_at = now()
            WHERE status = 'active'
              AND bid_date IS NOT NULL
              AND bid_date < $1
            """,
            date.today(),
        )
    log.info("Expiration sweep: %s", result)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())

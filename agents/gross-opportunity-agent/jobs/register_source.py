"""
Register (or update) one source from its config file into source_registry.
Generic version of the sam_gov block in jobs/seed.py — a new source is a config
file plus this one command, per the adapter doctrine. Idempotent upsert.

Run:  python -m jobs.register_source --config config/sources/emailed_bid_leads.json [--enable]
Env:  CLOUDSQL_DSN (or GOOGLE_CLOUD_PROJECT so it reads secret goa-cloudsql-dsn)

Without --enable the source lands verified='reported', enabled=false and waits
for a human APPROVE (Runbook Step 5); --enable records that approval.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pathlib

from goa.stores import cloudsql

log = logging.getLogger(__name__)

REPO = pathlib.Path(__file__).resolve().parent.parent


async def register(config_path: str, enable: bool) -> None:
    path = (REPO / config_path).resolve() if not pathlib.Path(config_path).is_absolute() \
        else pathlib.Path(config_path)
    cfg = json.loads(path.read_text(encoding="utf-8"))
    source_id = cfg["source_id"]

    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO source_registry
                (source_id, name, method, config, watermark_field, cadence_cron,
                 rate_limit, verified, enabled)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (source_id) DO UPDATE SET
                name = EXCLUDED.name,
                method = EXCLUDED.method,
                config = EXCLUDED.config,
                watermark_field = EXCLUDED.watermark_field,
                rate_limit = EXCLUDED.rate_limit,
                verified = EXCLUDED.verified,
                enabled = EXCLUDED.enabled
            """,
            source_id,
            cfg.get("name", source_id),
            cfg.get("method", "rest"),
            json.dumps(cfg),
            cfg.get("watermark_field"),
            cfg.get("cadence_cron"),
            json.dumps(cfg.get("rate_limit") or {}),
            "verified" if enable else "reported",
            enable,
        )
    log.info("Registered source %s (enabled=%s).", source_id, enable)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="path to config/sources/<id>.json")
    ap.add_argument("--enable", action="store_true",
                    help="mark verified+enabled (records the human APPROVE)")
    args = ap.parse_args()
    asyncio.run(register(args.config, args.enable))


if __name__ == "__main__":
    main()

"""
Seed job — loads the initial scope, the initial_screening rule list, and registers
the SAM.gov source into Cloud SQL. Idempotent: safe to re-run.

This is the real Runbook Step 2 (Manmeet). Scope values here reflect the division's
installed-sales focus (CSI 10 specialties, 08 openings, 22 plumbing) in the US. Where
the build spec left a value as a Gap (exact NAICS/keyword tuning), the seed uses the
defensible starting scope and marks the SAM.gov source verified='reported' + enabled=false
until a human APPROVE flips it on (Runbook Step 5).

Run:  python -m jobs.seed
Env:  CLOUDSQL_DSN (or GOOGLE_CLOUD_PROJECT so it reads secret goa-cloudsql-dsn)
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib

from goa.stores import cloudsql

log = logging.getLogger(__name__)

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent


# ── Initial scope (Data Contract v1.0 shape) ────────────────────────────────────
SCOPE = {
    "csi_divisions": [
        {"code": "10", "label": "Specialties (toilet partitions, shower enclosures)", "status": "active"},
        {"code": "08", "label": "Openings (glazing, shower doors)", "status": "active"},
        {"code": "22", "label": "Plumbing (fixtures)", "status": "active"},
    ],
    "product_scope": [
        "toilet partitions", "restroom partitions", "shower enclosures",
        "shower doors", "tub enclosures", "locker room partitions", "shower stalls",
    ],
    "project_types": [
        "active_bid", "itb", "planning_signal", "owner_pipeline", "permit_signal",
    ],
    "geographies": [
        # US state codes in scope. Nationwide default; narrow per territory later.
        "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA",
        "KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ",
        "NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT",
        "VA","WA","WV","WI","WY","DC",
    ],
    "hard_excludes": [
        "single-family residential",
        "single family home",
    ],
}


# ── Initial screening rules (initial_screening list) ─────────────────────────────
# kind: include|exclude ; operators match goa/gate/rules.py: matches|in|intersects|equals|gte|lte
RULES = [
    {
        "rule_id": "seed_exc_past_bid",
        "list_name": "initial_screening",
        "description": "Exclude opportunities whose bid date has already passed.",
        "kind": "exclude", "field": "bid_date", "operator": "lte", "value": "today",
        "source": "seeded", "active": True,
    },
    {
        "rule_id": "seed_exc_single_family",
        "list_name": "initial_screening",
        "description": "Exclude single-family residential-only work.",
        "kind": "exclude", "field": "project_name_and_body", "operator": "matches",
        "value": ["single-family", "single family residence", "single family home"],
        "source": "seeded", "active": True,
    },
    {
        "rule_id": "seed_inc_partitions",
        "list_name": "initial_screening",
        "description": "Include partition / shower / restroom specialty work.",
        "kind": "include", "field": "project_name_and_body", "operator": "matches",
        "value": ["partition", "shower", "restroom", "toilet compartment", "locker room"],
        "source": "seeded", "active": True,
    },
    {
        "rule_id": "seed_inc_csi_divisions",
        "list_name": "initial_screening",
        "description": "Include when detected CSI divisions intersect scope (10/08/22).",
        "kind": "include", "field": "csi_divisions", "operator": "intersects",
        "value": ["10", "08", "22"],
        "source": "seeded", "active": True,
    },
]


# ── SAM.gov source registration ─────────────────────────────────────────────────
def _sam_source_config() -> dict:
    with open(REPO / "config" / "sources" / "sam_gov.json") as f:
        cfg = json.load(f)
    # Strip comment keys before storing in the DB config JSON.
    return {k: v for k, v in cfg.items() if not k.startswith("_")}


async def seed_scope(conn) -> None:
    await conn.execute(
        """
        INSERT INTO scope (id, csi_divisions, product_scope, project_types, geographies, hard_excludes)
        VALUES (1, $1, $2, $3, $4, $5)
        ON CONFLICT (id) DO UPDATE SET
            csi_divisions = EXCLUDED.csi_divisions,
            product_scope = EXCLUDED.product_scope,
            project_types = EXCLUDED.project_types,
            geographies   = EXCLUDED.geographies,
            hard_excludes = EXCLUDED.hard_excludes
        """,
        json.dumps(SCOPE["csi_divisions"]),
        json.dumps(SCOPE["product_scope"]),
        json.dumps(SCOPE["project_types"]),
        json.dumps(SCOPE["geographies"]),
        json.dumps(SCOPE["hard_excludes"]),
    )
    log.info("Seeded scope (id=1).")


async def seed_rules(conn) -> None:
    for r in RULES:
        await conn.execute(
            """
            INSERT INTO screening_rule
                (rule_id, list_name, description, kind, field, operator, value, source, active)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (rule_id) DO UPDATE SET
                description = EXCLUDED.description,
                kind        = EXCLUDED.kind,
                field       = EXCLUDED.field,
                operator    = EXCLUDED.operator,
                value       = EXCLUDED.value,
                active      = EXCLUDED.active
            """,
            r["rule_id"], r["list_name"], r["description"], r["kind"],
            r["field"], r["operator"], json.dumps(r["value"]), r["source"], r["active"],
        )
    log.info("Seeded %d initial_screening rules.", len(RULES))


async def register_sam_source(conn) -> None:
    cfg = _sam_source_config()
    await conn.execute(
        """
        INSERT INTO source_registry
            (source_id, name, method, config, watermark_field, cadence_cron, rate_limit, verified, enabled)
        VALUES ($1,$2,$3,$4,$5,$6,$7,'reported', false)
        ON CONFLICT (source_id) DO UPDATE SET
            name            = EXCLUDED.name,
            method          = EXCLUDED.method,
            config          = EXCLUDED.config,
            watermark_field = EXCLUDED.watermark_field,
            cadence_cron    = EXCLUDED.cadence_cron,
            rate_limit      = EXCLUDED.rate_limit
        """,
        cfg["source_id"], cfg["name"], cfg["method"], json.dumps(cfg),
        cfg.get("watermark_field"),
        "30 0 * * *",  # once daily at 00:30 UTC — right after the SAM.gov quota reset
        json.dumps(cfg.get("rate_limit", {})),
    )
    log.info("Registered source sam_gov (enabled=false — awaits human APPROVE).")


async def run() -> None:
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await seed_scope(conn)
            await seed_rules(conn)
            await register_sam_source(conn)
    log.info("Seed complete.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    asyncio.run(run())

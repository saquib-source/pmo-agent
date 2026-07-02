"""
Fork router — routes non-opportunity records out of the lake.
Competitor and channel signal → competitor store.
Furnishing / FF&E firms → influence target list.
Section 6.7 of the build spec. Tier one, deterministic, no model.
"""

from __future__ import annotations
import logging
from typing import Any

from ..stores import cloudsql

log = logging.getLogger(__name__)

# Keyword-driven routing rules — Gap: extend with real competitor and FF&E lists
_COMPETITOR_KEYWORDS = [
    "competitor", "rival bid", "alternate vendor",
]
_FFE_KEYWORDS = [
    "furniture", "fixture", "equipment", "ff&e", "millwork",
    "casework", "shelving", "movable partition",
]


def classify_record(raw: dict) -> str:
    """Return routing target: 'opportunity' | 'competitor' | 'ffe' | 'other'."""
    text = " ".join([
        str(raw.get("title") or ""),
        str(raw.get("description") or ""),
        str(raw.get("synopsis") or ""),
    ]).lower()

    for kw in _COMPETITOR_KEYWORDS:
        if kw in text:
            return "competitor"

    for kw in _FFE_KEYWORDS:
        if kw in text:
            return "ffe"

    return "opportunity"


async def route_non_opportunities(source_id: str) -> None:
    """After a source pull, re-classify any records that may have been misrouted.
    Gap: wire to actual competitor store and influence target list when they exist.
    """
    pool = await cloudsql.get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT r.raw_id, r.payload, r.source_id
            FROM goa.raw_opportunity r
            WHERE r.source_id = $1
              AND r.ingested_at > now() - interval '2 hours'
            """,
            source_id,
        )
    for row in rows:
        import json
        raw = json.loads(row["payload"])
        target = classify_record(raw)
        if target != "opportunity":
            log.info("Fork router: routing raw_id=%s to %s", row["raw_id"], target)
            # Gap: insert into competitor_store or ffe_target table

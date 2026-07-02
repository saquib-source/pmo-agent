"""
Merge — collapse two records into one, keeping all source links and spec variants.
The surviving record keeps the earliest first_seen_at and the richest field set.
Merge never deletes the losing record's information.
"""

from __future__ import annotations
import logging
from datetime import datetime
from typing import Any

from ..schemas.canonical import CanonicalOpportunity, SourceLink
from ..stores import cloudsql

log = logging.getLogger(__name__)


def merge_into(winner: CanonicalOpportunity, loser: dict) -> CanonicalOpportunity:
    """Merge loser's fields into winner in-memory. Returns the enriched winner."""
    # Keep richest non-None fields from loser where winner is None
    if winner.project_name is None and loser.get("project_name"):
        winner.project_name = loser["project_name"]
    if winner.owner is None and loser.get("owner"):
        winner.owner = loser["owner"]
    if winner.valuation is None and loser.get("valuation"):
        winner.valuation = float(loser["valuation"])
    if winner.bid_date is None and loser.get("bid_date"):
        from datetime import date
        winner.bid_date = loser["bid_date"]
    if winner.primary_source_url is None and loser.get("primary_source_url"):
        winner.primary_source_url = loser["primary_source_url"]

    # Merge CSI divisions
    loser_csi = loser.get("csi_divisions") or []
    winner.csi_divisions = sorted(set(winner.csi_divisions or []) | set(loser_csi))

    # Keep earliest first_seen_at
    loser_first = loser.get("first_seen_at")
    if loser_first:
        if isinstance(loser_first, str):
            loser_first = datetime.fromisoformat(loser_first)
        if winner.first_seen_at is None or loser_first < winner.first_seen_at:
            winner.first_seen_at = loser_first

    winner.last_changed_at = datetime.utcnow()
    log.info("Merged opportunity %s into %s", loser.get("opportunity_id"), winner.opportunity_id)
    return winner


async def model_arbitrate(opp: CanonicalOpportunity, candidate: dict) -> bool:
    """Ask the dedup_ambiguous_merge engine whether these two records are the same project.
    Falls back to True (merge) on failure — recall-first policy applies to dedup too.
    Gap: wire to ADK model call once ADK runtime is available.
    """
    from ..stores.config_registry import get_version, get_family
    log.info(
        "Arbitrating merge via engine=%s %s for '%s' vs '%s'",
        get_family("dedup_ambiguous_merge"), get_version("dedup_ambiguous_merge"),
        opp.project_name, candidate.get("project_name"),
    )
    return False  # Stub — default to no-merge until ADK runtime is wired

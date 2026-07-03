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

    # Keep earliest first_seen_at. Cloud SQL returns tz-aware datetimes (TIMESTAMPTZ);
    # the normalizer builds tz-naive ones. Compare on a common basis (drop tzinfo).
    def _naive(dt):
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt)
        if dt is not None and dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt

    loser_first = _naive(loser.get("first_seen_at"))
    winner_first = _naive(winner.first_seen_at)
    if loser_first:
        if winner_first is None or loser_first < winner_first:
            winner.first_seen_at = loser_first
        else:
            winner.first_seen_at = winner_first

    winner.last_changed_at = datetime.utcnow()
    log.info("Merged opportunity %s into %s", loser.get("opportunity_id"), winner.opportunity_id)
    return winner


_ARBITRATE_SCHEMA = {
    "type": "object",
    "properties": {
        "same_project": {"type": "boolean"},
        "confidence": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["same_project", "confidence", "reason"],
    "additionalProperties": False,
}

_ARBITRATE_SYSTEM = (
    "You decide whether two commercial-construction opportunity records describe the "
    "SAME physical project (and should be merged), or two different projects. They came "
    "from different sources or notices and may differ in wording, valuation, or notice "
    "type. Merge only when the underlying project is clearly the same (same site/owner/"
    "scope). If genuinely unsure, prefer NOT merging — a false merge silently hides a real "
    "opportunity, which is worse than a duplicate a human can collapse. Return a boolean, "
    "a confidence in [0,1], and a one-line reason."
)


def _arbitrate_prompt(opp: CanonicalOpportunity, candidate: dict) -> str:
    return (
        "RECORD A (incoming):\n"
        f"  name: {opp.project_name}\n  owner: {opp.owner}\n"
        f"  address: {opp.address.street}, {opp.address.city}, {opp.address.state} {opp.address.postal_code}\n"
        f"  valuation: {opp.valuation}\n  bid_date: {opp.bid_date}\n  record_type: {opp.record_type}\n\n"
        "RECORD B (existing candidate):\n"
        f"  name: {candidate.get('project_name')}\n  owner: {candidate.get('owner')}\n"
        f"  address: {candidate.get('street')}, {candidate.get('city')}, {candidate.get('state')} {candidate.get('postal_code')}\n"
        f"  valuation: {candidate.get('valuation')}\n  bid_date: {candidate.get('bid_date')}\n  record_type: {candidate.get('record_type')}\n\n"
        "Do these describe the same physical project?"
    )


async def model_arbitrate(opp: CanonicalOpportunity, candidate: dict) -> bool:
    """Ask the dedup_ambiguous_merge engine whether these two records are the same project.
    On failure, default to NO merge (a false merge hides a real opportunity — worse than a dup).
    """
    import asyncio
    from ..engine import run_role_json
    try:
        result = await asyncio.to_thread(
            run_role_json, "dedup_ambiguous_merge", _ARBITRATE_SYSTEM,
            _arbitrate_prompt(opp, candidate), _ARBITRATE_SCHEMA,
        )
        same = bool(result.get("same_project"))
        log.info("Dedup arbitration: same_project=%s conf=%s '%s' vs '%s'",
                 same, result.get("confidence"), opp.project_name, candidate.get("project_name"))
        return same
    except Exception as e:
        log.warning("Dedup arbitration failed (%s) — not merging '%s' vs '%s'",
                    e, opp.project_name, candidate.get("project_name"))
        return False

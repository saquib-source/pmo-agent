"""
Coarse gate classifier — resolves the ambiguous middle via model call.
Recall-first: on low confidence or error, keep the record.
Config Registry role: gate_classifier → claude-fable-5 (confirmed by Manmeet).
Gap: wire to ADK model call once ADK runtime is available.
"""

from __future__ import annotations
import logging
from typing import Any

from ..stores.config_registry import get_family, get_version

log = logging.getLogger(__name__)

_KEEP_THRESHOLD = 0.40  # Below this confidence → keep (recall-first)


async def classify(opp: Any, scope: dict) -> dict:
    """Call the gate_classifier engine for one opportunity.
    Returns {passed: bool, score: float}.
    Recall-first: on error or confidence < threshold, keep.
    """
    engine_family = get_family("gate_classifier")
    engine_version = get_version("gate_classifier")
    log.info("Gate classifier: engine=%s %s opp=%s", engine_family, engine_version, opp.opportunity_id)

    try:
        # Stub — replace with actual ADK model call
        # The prompt would describe the scope and ask: is this opportunity relevant?
        # Until wired, return a neutral keep decision
        return {"passed": True, "score": 0.55}
    except Exception as e:
        log.warning("Gate classifier failed (%s) — keeping record (recall-first): %s", e, opp.opportunity_id)
        return {"passed": True, "score": _KEEP_THRESHOLD}


async def evaluate_with_classifier(opp: Any, rule_result: dict, scope: dict) -> dict:
    """Combine the rule engine result with a classifier call for the ambiguous band."""
    if not rule_result.get("needs_classifier"):
        return rule_result

    clf = await classify(opp, scope)
    passed = clf["score"] >= _KEEP_THRESHOLD  # recall-first: keep above threshold
    return {
        "passed": passed,
        "score": clf["score"],
        "matched_rules": rule_result.get("matched_rules", []),
        "needs_classifier": False,
    }

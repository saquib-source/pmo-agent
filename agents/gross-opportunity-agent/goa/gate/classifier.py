"""
Coarse gate classifier — resolves the ambiguous middle via a model call.
Recall-first: on low confidence or error, keep the record.
Engine binding resolves at runtime from the Config Registry role 'gate_classifier'
(config/engines.json). No vendor or model name appears in this file.
"""

from __future__ import annotations
import asyncio
import json
import logging
from typing import Any

from ..engine import run_role_json

log = logging.getLogger(__name__)

_KEEP_THRESHOLD = 0.40  # Below this confidence → keep (recall-first)

_CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "relevant": {"type": "boolean"},
        "score": {"type": "number"},
        "reason": {"type": "string"},
    },
    "required": ["relevant", "score", "reason"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are the coarse relevance gate for a commercial-construction opportunity "
    "pipeline. You screen ambiguous opportunities the deterministic rules could not "
    "resolve. Policy is RECALL-FIRST: when uncertain, keep. Only lean toward dropping "
    "on a clear out-of-scope signal (single-family residential only, or wholly unrelated "
    "trade). Deep qualification is a later stage's job, not yours. Return a relevance "
    "score in [0,1] where higher means more likely in scope, a boolean, and a one-line reason."
)


def _prompt(opp: Any, scope: dict) -> str:
    return (
        "SCOPE (what the division installs):\n"
        f"  CSI divisions: {scope.get('csi_divisions')}\n"
        f"  Product scope: {scope.get('product_scope')}\n"
        f"  Project types: {scope.get('project_types')}\n"
        f"  Geographies: {scope.get('geographies')}\n"
        f"  Hard excludes: {scope.get('hard_excludes')}\n\n"
        "OPPORTUNITY:\n"
        f"  Project name: {opp.project_name}\n"
        f"  Owner: {opp.owner}\n"
        f"  Record type: {opp.record_type}\n"
        f"  CSI divisions detected: {opp.csi_divisions}\n"
        f"  Location: {opp.address.city}, {opp.address.state}\n"
        f"  Valuation: {opp.valuation}\n\n"
        "Is this opportunity plausibly in scope for the division? Recall-first."
    )


async def classify(opp: Any, scope: dict) -> dict:
    """Call the gate_classifier engine for one opportunity.
    Returns {passed: bool, score: float}.
    Recall-first: on error, keep.
    """
    try:
        result = await asyncio.to_thread(
            run_role_json, "gate_classifier", _SYSTEM, _prompt(opp, scope), _CLASSIFY_SCHEMA
        )
        score = float(result.get("score", _KEEP_THRESHOLD))
        # Recall-first: honour an explicit relevant=true, else fall back to the score band.
        passed = bool(result.get("relevant")) or score >= _KEEP_THRESHOLD
        return {"passed": passed, "score": score, "reason": result.get("reason", ""), "engine": "model"}
    except Exception as e:
        log.warning("Gate classifier failed (%s) — keeping record (recall-first): %s", e, opp.opportunity_id)
        return {"passed": True, "score": _KEEP_THRESHOLD, "reason": f"classifier error, kept (recall-first): {e}", "engine": "fallback"}


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
        "classifier_reason": clf.get("reason", ""),
        "classifier_engine": clf.get("engine", ""),
    }

"""
Coarse gate rule evaluation engine.
Applies the active initial_screening rules and the scope to one opportunity.
Section 10 of the build spec governs evaluation order and recall-first semantics.

Evaluation order:
  1. Apply exclude rules. Any active exclude match drops immediately.
  2. Apply include rules and scope. If any match, keep.
  3. If neither excludes nor includes resolve → send to classifier.
  4. On classifier low confidence → keep (recall-first).
"""

from __future__ import annotations
import json
import logging
import re
from datetime import date, datetime
from typing import Any

log = logging.getLogger(__name__)

# Score assigned by rule matches when no classifier is used
_INCLUDE_SCORE = 0.75
_SCOPE_MATCH_SCORE = 0.65
_UNRESOLVED_SCORE = 0.50


def _val(rule_value: Any) -> Any:
    """Parse JSONB value from DB (may arrive as string)."""
    if isinstance(rule_value, str):
        try:
            return json.loads(rule_value)
        except (json.JSONDecodeError, ValueError):
            return rule_value
    return rule_value


def _field_value(opp: Any, field: str) -> Any:
    """Extract the field value from a CanonicalOpportunity by dotted path."""
    if field == "project_name_and_body":
        return f"{opp.project_name or ''} {opp.owner or ''}"
    if field == "csi_divisions":
        return opp.csi_divisions
    if field == "project_type":
        return opp.record_type
    if field == "bid_date":
        return opp.bid_date
    if field.startswith("normalized_address."):
        sub = field.split(".", 1)[1]
        return getattr(opp.address, sub, None)
    return getattr(opp, field, None)


def _evaluate_rule(rule: dict, opp: Any) -> bool | None:
    """Evaluate one rule against an opportunity. Returns True/False or None if undecidable."""
    field_val = _field_value(opp, rule["field"])
    op = rule["operator"]
    rule_val = _val(rule.get("value"))

    if op == "matches":
        text = str(field_val or "").lower()
        if isinstance(rule_val, list):
            return any(kw.lower() in text for kw in rule_val)
        return bool(rule_val and str(rule_val).lower() in text)

    if op == "in":
        if not rule_val:
            return True  # empty list means "pass all"
        return str(field_val or "").lower() in [str(v).lower() for v in rule_val]

    if op == "intersects":
        if not rule_val:
            return True  # empty scope list means "pass all"
        haystack = field_val or []
        return bool(set(haystack) & set(rule_val))

    if op == "equals":
        return field_val == rule_val

    if op in ("gte", "lte"):
        if rule_val == "today":
            rule_val = date.today()
        try:
            a = float(field_val) if not isinstance(field_val, (date, datetime)) else field_val
            b = float(rule_val) if not isinstance(rule_val, (date, datetime)) else rule_val
            return a >= b if op == "gte" else a <= b
        except (TypeError, ValueError):
            return None

    log.warning("Unknown operator: %s", op)
    return None


def evaluate(opp: Any, rules: list[dict], scope: dict) -> dict:
    """Evaluate all rules and scope against an opportunity.
    Returns {passed, score, matched_rules, needs_classifier}.
    """
    active_rules = [r for r in rules if r.get("active", True)]
    exclude_rules = [r for r in active_rules if r["kind"] == "exclude"]
    include_rules = [r for r in active_rules if r["kind"] == "include"]

    matched: list[str] = []

    # Phase 1 — exclude pass
    for rule in exclude_rules:
        result = _evaluate_rule(rule, opp)
        if result is True:
            matched.append(rule["rule_id"])
            log.debug("Gate: EXCLUDE match rule=%s opp=%s", rule["rule_id"], opp.opportunity_id)
            return {"passed": False, "score": 0.0, "matched_rules": matched, "needs_classifier": False}

    # Phase 2 — include + scope
    include_matched = False
    for rule in include_rules:
        result = _evaluate_rule(rule, opp)
        if result is True:
            matched.append(rule["rule_id"])
            include_matched = True

    # Scope interplay — each scope dimension is an implicit include with "pass all if empty"
    scope_dims = {
        "csi_divisions": ("csi_divisions", "intersects"),
        "project_types": ("project_type", "in"),
        "geographies": ("normalized_address.state", "in"),
    }
    scope_match = True
    for dim, (field, op) in scope_dims.items():
        dim_val = scope.get(dim, [])
        result = _evaluate_rule({"field": field, "operator": op, "value": dim_val, "rule_id": f"scope_{dim}"}, opp)
        if result is False:
            scope_match = False

    # Hard excludes from scope
    hard_excludes = scope.get("hard_excludes", [])
    if hard_excludes:
        for exclude_term in hard_excludes:
            text = str(opp.project_name or "").lower()
            if str(exclude_term).lower() in text:
                return {"passed": False, "score": 0.0, "matched_rules": ["scope_hard_exclude"], "needs_classifier": False}

    if include_matched and scope_match:
        score = _INCLUDE_SCORE + 0.05 * len(matched)
        return {"passed": True, "score": min(score, 0.99), "matched_rules": matched, "needs_classifier": False}

    if not include_matched and not scope_match:
        # Nothing resolved — send to classifier
        return {"passed": None, "score": _UNRESOLVED_SCORE, "matched_rules": [], "needs_classifier": True}

    # Partial match — keep but send to classifier for scoring
    return {"passed": None, "score": _SCOPE_MATCH_SCORE, "matched_rules": matched, "needs_classifier": True}

"""
Blocking and fuzzy match for the dedup engine.
Step 1: exact key match (handled by keying.py + the DB unique index).
Step 2: blocking on address + bid_date — narrows candidates without scanning everything.
Step 3: fuzzy match on project_name — Levenshtein ratio.
Step 4: embedding compare for the ambiguous band (see embed.py).
"""

from __future__ import annotations
import logging
from typing import Any

log = logging.getLogger(__name__)

# Band thresholds — tune with real data
_CERTAIN_MATCH = 0.90
_AMBIGUOUS_FLOOR = 0.72


def _lev_ratio(a: str, b: str) -> float:
    """Levenshtein similarity ratio in [0, 1]. Pure Python, no dependency."""
    a, b = a.lower(), b.lower()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    m, n = len(a), len(b)
    dp = list(range(n + 1))
    for i in range(1, m + 1):
        prev = dp[:]
        dp[0] = i
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[j] = prev[j - 1]
            else:
                dp[j] = 1 + min(prev[j], dp[j - 1], prev[j - 1])
    return 1 - dp[n] / max(m, n)


async def blocking_candidates(opp: Any, db_get_fn) -> list[dict]:
    """Return rows from Cloud SQL that share city + bid_date (the blocking key).
    db_get_fn is a coroutine: async (city, bid_date) -> list[dict]
    """
    return await db_get_fn(opp.address.city, opp.bid_date)


def score_candidates(opp: Any, candidates: list[dict]) -> list[tuple[float, dict]]:
    """Score each blocking candidate by project_name similarity."""
    name = (opp.project_name or "").lower()
    scored = []
    for cand in candidates:
        ratio = _lev_ratio(name, (cand.get("project_name") or "").lower())
        scored.append((ratio, cand))
    return sorted(scored, key=lambda x: x[0], reverse=True)


def partition_by_confidence(scored: list[tuple[float, dict]]) -> tuple[list[dict], list[dict]]:
    """Split into certain matches and the ambiguous band."""
    certain = [c for score, c in scored if score >= _CERTAIN_MATCH]
    ambiguous = [c for score, c in scored if _AMBIGUOUS_FLOOR <= score < _CERTAIN_MATCH]
    return certain, ambiguous

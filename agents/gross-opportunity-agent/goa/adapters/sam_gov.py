"""
SAM.gov adapter — federal opportunity board REST API.
This is the worked example per Section 6.2 of the build spec.

Request economics (free key = 10 requests/day, registered role = 1,000/day):
  Every search page and every fetch_full is ONE request against the daily quota.
  This adapter never decides how many requests it may make — the orchestrator reads
  rate_limit.requests_per_day from the source config (env GOA_REQUESTS_PER_DAY wins),
  subtracts what the persisted ledger says was already used today, and hands the
  adapter a budget. The adapter raises BudgetExhausted BEFORE the request that would
  exceed it, so SAM.gov never sees enough traffic to 429 us.

Query plan (config query_plan.keywords / query_plan.naics):
  Each keyword and each NAICS code is one search loop. With limit=1000 a scoped
  keyword search is almost always a single request, so N keywords ≈ N requests/day.
  Raising the quota tomorrow = edit requests_per_day (or set GOA_REQUESTS_PER_DAY)
  and, optionally, add more keywords/NAICS — no code change.
"""

import logging
import os
from datetime import date, timedelta
from typing import Iterator

from .rest_adapter import RestAdapter

log = logging.getLogger(__name__)

# Map SAM.gov v2 notice type (a human-readable string) to GOA record_type.
_TYPE_TO_RECORD_TYPE = {
    "Solicitation": "itb",
    "Presolicitation": "active_bid",
    "Combined Synopsis/Solicitation": "active_bid",
    "Sources Sought": "planning_signal",
    "Special Notice": "planning_signal",
    "Award Notice": "planning_signal",
}
_BIDDING_TYPES = {"Solicitation", "Combined Synopsis/Solicitation"}


class SamGovAdapter(RestAdapter):
    def _date_window(self, mode: str, watermark: str | None) -> dict:
        """SAM.gov search REQUIRES a posted-date window (MM/dd/yyyy), max 1 year.
        backfill: trailing GOA_SAM_BACKFILL_DAYS (default 365) days.
        delta: since watermark minus 1-day overlap (dedup absorbs the overlap),
        or trailing 14 days when no watermark exists yet."""
        today = date.today()
        if mode == "backfill":
            days = int(os.environ.get("GOA_SAM_BACKFILL_DAYS", "365"))
            start = today - timedelta(days=days)
        else:
            start = None
            if watermark:
                try:
                    start = date.fromisoformat(watermark[:10]) - timedelta(days=1)
                except ValueError:
                    start = None
            if start is None:
                start = today - timedelta(days=14)
        fmt = "%m/%d/%Y"
        return {"postedFrom": start.strftime(fmt), "postedTo": today.strftime(fmt)}

    def extra_params(self, mode: str, watermark: str | None) -> dict:
        params = self._date_window(mode, watermark)
        kw = os.environ.get("GOA_SAM_KEYWORD")
        if kw:
            params["title"] = kw  # SAM.gov title keyword filter
        return params

    def param_variants(self, mode: str, watermark: str | None) -> list[dict]:
        """One search per configured keyword + one per NAICS code. Precedence:
        GOA_SAM_KEYWORD env (single scoped search, for tests) > config query_plan >
        single unfiltered search (NOT recommended on a 10/day key — it paginates the
        whole firehose)."""
        window = self._date_window(mode, watermark)
        env_kw = os.environ.get("GOA_SAM_KEYWORD")
        if env_kw:
            return [{**window, "title": env_kw}]

        plan = self.cfg.get("query_plan") or {}
        variants: list[dict] = []
        for kw in plan.get("keywords") or []:
            variants.append({**window, "title": kw})
        for code in plan.get("naics") or []:
            variants.append({**window, "ncode": str(code)})
        return variants or [window]

    def _advanced_cursor(self, mode: str, watermark: str | None):
        """After a fully-successful pull the watermark becomes today, so the next
        delta only asks SAM for notices posted since the last good run."""
        return date.today().isoformat()

    def pull(self, mode: str, watermark: str | None) -> Iterator[dict]:
        for raw in super().pull(mode, watermark):
            # SAM.gov v2 'type' is a human-readable string (e.g. "Solicitation").
            ntype = raw.get("type")
            if isinstance(ntype, dict):  # tolerate the older {"value": ...} shape
                ntype = ntype.get("value")
            ntype = ntype or "Solicitation"
            raw["_derived_from_ptype_record_type"] = _TYPE_TO_RECORD_TYPE.get(ntype, "active_bid")
            raw["_derived_from_ptype_stage"] = "bidding" if ntype in _BIDDING_TYPES else "planning"
            yield raw

    def fetch_full(self, source_record_id: str) -> tuple[dict, list[dict]]:
        url = "https://api.sam.gov/opportunities/v2/search"
        params = dict(self._auth_query)  # api_key
        params["noticeid"] = source_record_id
        resp = self._get(url, params)  # budget-charged + throttled + 429-safe
        data = resp.json()
        opportunities = data.get("opportunitiesData", [])
        full = opportunities[0] if opportunities else {}
        variants = []
        for link in (full.get("resourceLinks") or []):
            if isinstance(link, dict):
                variants.append({
                    "source_name": "SAM.gov",
                    "label": link.get("text", "attachment"),
                    "url": link.get("uri"),
                })
            elif isinstance(link, str):  # v2 often returns bare URL strings
                variants.append({"source_name": "SAM.gov",
                                 "label": link.rsplit("/", 2)[-2] if "/" in link else "attachment",
                                 "url": link})
        return full, variants

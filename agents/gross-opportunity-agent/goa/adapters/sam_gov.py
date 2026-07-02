"""
SAM.gov adapter — federal opportunity board REST API.
This is the worked example per Section 6.2 of the build spec.
All source-specific values come from config/sources/sam_gov.json.
Gap: confirm NAICS codes and keywords for shower/partition scope.
"""

import logging
from typing import Iterator

from .rest_adapter import RestAdapter

log = logging.getLogger(__name__)

# Map SAM.gov ptype codes to GOA record_type
_PTYPE_TO_RECORD_TYPE = {
    "o": "itb",               # Solicitation
    "p": "active_bid",        # Pre-Solicitation
    "k": "active_bid",        # Combined Synopsis/Solicitation
    "r": "planning_signal",   # Sources Sought
    "s": "planning_signal",   # Special Notice
}


class SamGovAdapter(RestAdapter):
    def pull(self, mode: str, watermark: str | None) -> Iterator[dict]:
        for raw in super().pull(mode, watermark):
            # Annotate with derived fields so the normalizer can read them
            ptype = (raw.get("type") or {}).get("value", "o")
            raw["_derived_from_ptype_record_type"] = _PTYPE_TO_RECORD_TYPE.get(ptype, "active_bid")
            raw["_derived_from_ptype_stage"] = "bidding" if ptype in ("o", "k") else "planning"
            yield raw

    def fetch_full(self, source_record_id: str) -> tuple[dict, list[dict]]:
        import requests
        url = f"https://api.sam.gov/opportunities/v2/search?noticeid={source_record_id}"
        self._rate_limit_sleep()
        resp = requests.get(url, headers=self._headers, timeout=30)
        resp.raise_for_status()
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
        return full, variants

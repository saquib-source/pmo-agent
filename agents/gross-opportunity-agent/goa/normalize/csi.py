"""
CSI division extraction from project names, descriptions, and spec text.
Returns a list of two-digit CSI division codes ("10", "08", "22", …).
Gap: review and extend the keyword dictionary against the scope divisions.
"""

import re

# Primary keyword → CSI division mapping
# Gap: validate this dictionary against the actual scope (CSI 10, 08, 22 + neighbours)
_KEYWORDS: dict[str, str] = {
    # Division 10 — Specialties
    "shower": "10", "shower door": "10", "shower enclosure": "10",
    "glass shower": "10", "tub enclosure": "10",
    "toilet partition": "10", "restroom partition": "10", "bathroom partition": "10",
    "shower partition": "10", "shower room": "10",
    "locker": "10", "cubicle": "10", "signage": "10",
    # Division 08 — Openings
    "door": "08", "window": "08", "glazing": "08", "curtain wall": "08",
    "storefronts": "08", "glass door": "08", "entrance": "08", "overhead door": "08",
    # Division 22 — Plumbing
    "plumbing": "22", "fixture": "22", "bathroom": "22", "restroom": "22",
    "toilet": "22", "urinal": "22", "lavatory": "22", "sink": "22",
    # Division 03 — Concrete (often paired)
    "concrete": "03", "rebar": "03",
    # Division 09 — Finishes
    "tile": "09", "ceramic": "09", "flooring": "09", "paint": "09", "drywall": "09",
}


def extract_csi_divisions(text: str) -> list[str]:
    """Extract CSI division codes from free-form text. Returns deduplicated sorted list."""
    if not text:
        return []
    lower = text.lower()
    found: set[str] = set()
    for keyword, division in _KEYWORDS.items():
        if keyword in lower:
            found.add(division)
    # Also capture explicit CSI references like "Division 10" or "Spec Section 08"
    for match in re.finditer(r"\bdivision\s+(\d{2})\b|\bspec(?:ification)?\s+section\s+(\d{2})\b", lower):
        code = match.group(1) or match.group(2)
        if code:
            found.add(code.zfill(2))
    return sorted(found)

"""
Address normalization — expand abbreviations, lowercase, strip unit numbers.
Keeps enough structure so the dedup composite key is stable across sources.
"""

import re

_ABBREV = {
    "st": "street", "ave": "avenue", "blvd": "boulevard", "dr": "drive",
    "rd": "road", "ln": "lane", "ct": "court", "pl": "place",
    "hwy": "highway", "pkwy": "parkway", "n": "north", "s": "south",
    "e": "east", "w": "west", "ne": "northeast", "nw": "northwest",
    "se": "southeast", "sw": "southwest",
}

_STATE_ABBREV = {
    "al","ak","az","ar","ca","co","ct","de","fl","ga","hi","id","il","in","ia",
    "ks","ky","la","me","md","ma","mi","mn","ms","mo","mt","ne","nv","nh","nj",
    "nm","ny","nc","nd","oh","ok","or","pa","ri","sc","sd","tn","tx","ut","vt",
    "va","wa","wv","wi","wy","dc",
}


def _as_text(v) -> str:
    """Coerce a field value to text. Some sources (SAM.gov) return address parts as
    either a plain string or a {name}/{code}/{value} dict, inconsistently per record."""
    if v is None:
        return ""
    if isinstance(v, dict):
        v = v.get("name") or v.get("code") or v.get("value") or ""
    return str(v)


def normalize_street(street) -> str:
    s = _as_text(street)
    if not s:
        return ""
    s = s.lower().strip()
    # Strip unit / suite / apt designations
    s = re.sub(r"\b(suite|ste|apt|unit|#)\s*\S+", "", s)
    tokens = s.split()
    expanded = [_ABBREV.get(t, t) for t in tokens]
    return " ".join(expanded).strip()


def normalize_city(city) -> str:
    return _as_text(city).lower().strip()


def normalize_state(state) -> str:
    s = _as_text(state).lower().strip()
    return s if s in _STATE_ABBREV else s


def normalize_address_str(address) -> str:
    """Return a single lowercase string suitable for use in the composite key."""
    parts = [
        normalize_street(address.street),
        normalize_city(address.city),
        normalize_state(address.state),
        (address.postal_code or "").strip(),
    ]
    return " ".join(p for p in parts if p)

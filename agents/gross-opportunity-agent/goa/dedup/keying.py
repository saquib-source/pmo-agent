"""
Project identity key — stable SHA-256 hash of the five composite fields.
Two records that describe the same project collapse to the same key.
"""

import hashlib
from datetime import date
from ..normalize.address import normalize_address_str
from ..schemas.canonical import Address


def _value_bucket(valuation: float | None) -> str:
    """Coarse bucket so small value differences still match."""
    if valuation is None:
        return ""
    if valuation < 250_000:
        return "<250k"
    if valuation < 1_000_000:
        return "250k-1m"
    if valuation < 5_000_000:
        return "1m-5m"
    if valuation < 20_000_000:
        return "5m-20m"
    return ">20m"


def _norm_text(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(text.lower().split())


def project_identity_key(
    address: Address,
    owner: str | None,
    project_name: str | None,
    valuation: float | None,
    bid_date: date | None,
) -> str:
    parts = [
        normalize_address_str(address),
        _norm_text(owner),
        _norm_text(project_name),
        _value_bucket(valuation),
        bid_date.isoformat() if bid_date else "",
    ]
    composite = "|".join(p or "" for p in parts)
    return hashlib.sha256(composite.encode()).hexdigest()

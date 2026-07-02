"""
Normalizer — maps one raw source record to CanonicalOpportunity.
Uses a deterministic field map from the source config. Calls the extraction model
(via Config Registry) only for fields the field map cannot resolve.
"""

import hashlib
import json
import logging
import uuid
from datetime import date, datetime
from typing import Any

from ..schemas.canonical import Address, CanonicalOpportunity, SourceLink
from .address import normalize_address_str, normalize_city, normalize_state, normalize_street
from .csi import extract_csi_divisions
from ..stores.config_registry import get_version, get_family

log = logging.getLogger(__name__)


def _get(raw: dict, path: str) -> Any:
    """Dot-notation accessor for nested raw dicts."""
    parts = path.split(".")
    v = raw
    for p in parts:
        if not isinstance(v, dict):
            return None
        v = v.get(p)
    return v


def _parse_date(raw_date: Any) -> date | None:
    if raw_date is None:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(str(raw_date)[:19], fmt).date()
        except ValueError:
            continue
    return None


def _extraction_model_fill(field: str, text: str) -> Any:
    """Call the extraction model for one field value the field map couldn't resolve.
    Uses config_registry role 'normalizer_extraction' — no vendor string in this code.
    Gap: wire to ADK model call once the ADK runtime is available.
    """
    log.debug("Extraction model called for field=%s (engine=%s %s)", field, get_family("normalizer_extraction"), get_version("normalizer_extraction"))
    return None  # Stub — returns None until ADK runtime is wired


def normalize(raw: dict, source_cfg: dict) -> CanonicalOpportunity:
    field_map = source_cfg.get("field_map", {})
    source_id = source_cfg["source_id"]

    def f(field_key: str) -> Any:
        mapped = field_map.get(field_key)
        if mapped and not mapped.startswith("_derived"):
            return _get(raw, mapped)
        return None

    project_name = f("project_name") or ""
    owner = f("owner") or ""
    valuation_raw = f("valuation")
    valuation = float(valuation_raw) if valuation_raw not in (None, "", "0") else None
    bid_date = _parse_date(f("bid_date"))
    primary_source_url = f("primary_source_url") or ""
    source_record_id = f("source_record_id") or str(raw.get("id", ""))

    address = Address(
        street=normalize_street(f("street")),
        city=normalize_city(f("city")),
        state=normalize_state(f("state")),
        postal_code=str(f("postal_code") or "").strip(),
        country=str(f("country") or "US").strip(),
    )

    # CSI extraction: try field map, then keyword extraction from name + any description
    csi = extract_csi_divisions(
        f"project_name:{project_name} " +
        str(raw.get("description") or raw.get("synopsis") or "")
    )

    # record_type from derived field annotation or field map
    record_type = (
        raw.get("_derived_from_ptype_record_type")
        or f("record_type")
        or "active_bid"
    )
    stage = (
        raw.get("_derived_from_ptype_stage")
        or f("stage")
        or "bidding"
    )

    # Extraction model fallback for bid_date if field map gave nothing
    if bid_date is None:
        raw_text = json.dumps(raw)
        extracted = _extraction_model_fill("bid_date", raw_text)
        bid_date = _parse_date(extracted)

    # Composite project identity key
    from ..dedup.keying import project_identity_key
    pik = project_identity_key(address, owner, project_name, valuation, bid_date)

    now = datetime.utcnow()
    return CanonicalOpportunity(
        opportunity_id=str(uuid.uuid4()),  # temporary; dedup will assign or merge
        project_identity_key=pik,
        project_name=project_name or None,
        record_type=record_type,
        stage=stage,
        address=address,
        owner=owner or None,
        valuation=valuation,
        bid_date=bid_date,
        csi_divisions=csi,
        primary_source_url=primary_source_url or None,
        source_links=[SourceLink(
            source_name=source_cfg.get("name", source_id),
            source_url=primary_source_url or None,
            source_record_id=source_record_id or None,
        )],
        first_seen_at=now,
        last_changed_at=now,
    )

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Address:
    street: str | None = None
    city: str | None = None
    state: str | None = None
    postal_code: str | None = None
    country: str | None = None


@dataclass
class SourceLink:
    source_name: str = ""
    source_url: str | None = None
    source_record_id: str | None = None


@dataclass
class CanonicalOpportunity:
    opportunity_id: str = ""               # assigned at dedup
    project_identity_key: str = ""         # SHA-256 composite key
    project_name: str | None = None
    record_type: str = ""                  # active_bid | itb | permit_signal | owner_pipeline | planning_signal
    stage: str | None = None
    status: str = "active"                 # active | closed | rejected
    address: Address = field(default_factory=Address)
    owner: str | None = None
    valuation: float | None = None
    bid_date: date | None = None
    csi_divisions: list[str] = field(default_factory=list)
    primary_source_url: str | None = None
    source_links: list[SourceLink] = field(default_factory=list)
    gate_passed: bool | None = None
    gate_score: float | None = None
    gate_matched_rules: list[str] = field(default_factory=list)
    closed_reason: str | None = None       # expired | withdrawn | awarded_elsewhere
    first_seen_at: datetime | None = None
    last_changed_at: datetime | None = None
    fetch_state: str = "summary"           # summary | pulling | full_pulled | failed
    fetch_error: dict | None = None        # {code, message, failed_at}
    full_record: dict | None = None

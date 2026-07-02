"""
BigQuery store — raw landing, normalized copy, analytics gross_opportunity.
The screen does NOT read BigQuery. This is the lake layer only.
Gap: GCP project id and dataset location.
"""

import logging
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

# Gap: set via environment or Secret Manager reference
_PROJECT = None   # os.environ["GOOGLE_CLOUD_PROJECT"]
_DATASET = "goa"


def _client():
    from google.cloud import bigquery  # type: ignore
    return bigquery.Client(project=_PROJECT)


def insert_raw(source_id: str, source_record_id: str | None, payload: dict, pull_mode: str) -> str:
    """Land one raw record. Returns the raw_id assigned."""
    import hashlib, json
    raw_id = hashlib.sha256(
        f"{source_id}:{source_record_id}:{json.dumps(payload, sort_keys=True)}".encode()
    ).hexdigest()
    row = {
        "raw_id": raw_id,
        "source_id": source_id,
        "source_record_id": source_record_id,
        "payload": json.dumps(payload),
        "pull_mode": pull_mode,
        "ingested_at": datetime.utcnow().isoformat(),
    }
    _client().insert_rows_json(f"{_PROJECT}.{_DATASET}.raw_opportunity", [row])
    return raw_id


def insert_normalized(normalized_id: str, raw_id: str, source_id: str, rec: Any) -> None:
    """Write the normalized record to BigQuery after normalization."""
    row = {
        "normalized_id": normalized_id,
        "raw_id": raw_id,
        "source_id": source_id,
        "project_identity_key": rec.project_identity_key,
        "project_name": rec.project_name,
        "record_type": rec.record_type,
        "stage": rec.stage,
        "street": rec.address.street,
        "city": rec.address.city,
        "state": rec.address.state,
        "postal_code": rec.address.postal_code,
        "country": rec.address.country,
        "owner": rec.owner,
        "valuation": float(rec.valuation) if rec.valuation else None,
        "bid_date": rec.bid_date.isoformat() if rec.bid_date else None,
        "csi_divisions": rec.csi_divisions,
        "primary_source_url": rec.primary_source_url,
        "normalized_at": datetime.utcnow().isoformat(),
    }
    _client().insert_rows_json(f"{_PROJECT}.{_DATASET}.normalized_opportunity", [row])


def upsert_gross(opp: Any) -> None:
    """Sync one deduplicated opportunity to the analytics gross_opportunity table.
    BigQuery does not support native upsert — merge via MERGE DML or streaming + periodic compaction.
    """
    row = {
        "opportunity_id": opp.opportunity_id,
        "project_identity_key": opp.project_identity_key,
        "project_name": opp.project_name,
        "record_type": opp.record_type,
        "stage": opp.stage,
        "status": opp.status,
        "city": opp.address.city,
        "state": opp.address.state,
        "valuation": float(opp.valuation) if opp.valuation else None,
        "bid_date": opp.bid_date.isoformat() if opp.bid_date else None,
        "csi_divisions": opp.csi_divisions,
        "gate_passed": opp.gate_passed,
        "gate_score": float(opp.gate_score) if opp.gate_score is not None else None,
        "first_seen_at": opp.first_seen_at.isoformat() if opp.first_seen_at else None,
        "last_changed_at": opp.last_changed_at.isoformat() if opp.last_changed_at else None,
    }
    _client().insert_rows_json(f"{_PROJECT}.{_DATASET}.gross_opportunity", [row])
    log.debug("BigQuery: upserted gross_opportunity %s", opp.opportunity_id)

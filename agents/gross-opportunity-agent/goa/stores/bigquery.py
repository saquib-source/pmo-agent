"""
BigQuery store — raw landing, normalized copy, analytics gross_opportunity.
The screen does NOT read BigQuery. This is the lake layer only.
Gap: GCP project id and dataset location.
"""

import asyncio
import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any

log = logging.getLogger(__name__)

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
_DATASET = os.environ.get("GOA_BQ_DATASET", "goa")
# GOA_SKIP_LAKE=1 disables BigQuery writes (for local runs where the BQ gRPC client is
# proxy-blocked). The lake is the analytics copy, not the serving path — skipping it
# locally does not affect the review queue. Unset in production (Cloud Run).
_SKIP = os.environ.get("GOA_SKIP_LAKE") == "1"
_bq_client = None


def _client():
    global _bq_client
    if _bq_client is None:
        from google.cloud import bigquery  # type: ignore
        project = _PROJECT or os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is not set — cannot write to BigQuery")
        _bq_client = bigquery.Client(project=project)
    return _bq_client


def _table(name: str) -> str:
    project = _PROJECT or os.environ.get("GOOGLE_CLOUD_PROJECT")
    return f"{project}.{_DATASET}.{name}"


def _insert_rows(table: str, rows: list[dict]) -> None:
    errors = _client().insert_rows_json(_table(table), rows)
    if errors:
        # Surface real BigQuery insert errors — do not swallow.
        raise RuntimeError(f"BigQuery insert into {table} failed: {errors}")


async def insert_raw(source_id: str, source_record_id: str | None, payload: dict, pull_mode: str) -> str:
    """Land one raw record. Returns the raw_id assigned. Async wrapper over the BQ client."""
    raw_id = hashlib.sha256(
        f"{source_id}:{source_record_id}:{json.dumps(payload, sort_keys=True)}".encode()
    ).hexdigest()
    if _SKIP:
        log.debug("BigQuery skipped (GOA_SKIP_LAKE=1) for raw %s", raw_id[:12])
        return raw_id
    row = {
        "raw_id": raw_id,
        "source_id": source_id,
        "source_record_id": source_record_id,
        "payload": json.dumps(payload),
        "pull_mode": pull_mode,
        "ingested_at": datetime.utcnow().isoformat(),
    }
    await asyncio.to_thread(_insert_rows, "raw_opportunity", [row])
    return raw_id


async def insert_normalized(normalized_id: str, raw_id: str, source_id: str, rec: Any) -> None:
    """Write the normalized record to BigQuery after normalization."""
    if _SKIP:
        return
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
    await asyncio.to_thread(_insert_rows, "normalized_opportunity", [row])


async def upsert_gross(opp: Any) -> None:
    """Stream one deduplicated opportunity to the analytics gross_opportunity table.
    BigQuery has no native upsert — this streams an append; analytics dedups by
    opportunity_id + last_changed_at (latest wins), or run periodic MERGE compaction.
    """
    if _SKIP:
        log.debug("BigQuery skipped (GOA_SKIP_LAKE=1) for gross %s", opp.opportunity_id)
        return
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
    await asyncio.to_thread(_insert_rows, "gross_opportunity", [row])
    log.debug("BigQuery: streamed gross_opportunity %s", opp.opportunity_id)

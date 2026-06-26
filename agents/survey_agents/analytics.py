"""
BigQuery analytics writer for the Survey Agent package.

Mirrors agents/pmo-swarm/adk/shared/analytics.py: all agent-generated analytics
go to BigQuery (dataset isrds_pmo), never Postgres. The Survey Agent is a separate
deployable package, so it carries its own minimal writer rather than importing the
PMO module.

The tool_call_audit table was dropped from Postgres in migration
006_pmo_storage_routing.sql; log_tool_call_audit() is its replacement.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

_DATASET = "isrds_pmo"
_bq_client = None


def _project() -> str:
    return os.environ.get("GOOGLE_CLOUD_PROJECT", "isr-division-systems-488723")


def _tenant() -> str:
    return os.environ.get("TENANT_ID", "ashs")


def _client():
    global _bq_client
    if _bq_client is None:
        try:
            from google.cloud import bigquery
            _bq_client = bigquery.Client(project=_project())
        except Exception as e:
            log.warning(f"BigQuery: client init failed ({e}) — analytics writes disabled")
            _bq_client = False
    return _bq_client if _bq_client else None


def _table(name: str) -> str:
    return f"{_project()}.{_DATASET}.{name}"


def _insert(table_name: str, rows: list) -> None:
    bq = _client()
    if bq is None or not rows:
        return
    try:
        errs = bq.insert_rows_json(_table(table_name), rows)
        if errs:
            log.warning(f"BigQuery {table_name} insert errors: {errs}")
    except Exception as e:
        log.warning(f"BigQuery: {table_name} write failed ({e})")


def hash_payload(data: Any) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()[:16]


async def log_tool_call_audit(
    trace_id: str,
    role_category: str,
    tool_name: str,
    status: str,
    duration_ms: Optional[int] = None,
    input_hash: Optional[str] = None,
    output_hash: Optional[str] = None,
) -> None:
    """Record a single tool invocation to BigQuery isrds_pmo.tool_call_audit.
    Replaces the dropped Postgres tool_call_audit table.
    status: 'SUCCESS' | 'ERROR' | 'TIMEOUT'
    """
    _insert("tool_call_audit", [{
        "event_ts":      datetime.now(timezone.utc).isoformat(),
        "tenant_id":     _tenant(),
        "trace_id":      trace_id,
        "role_category": role_category,
        "tool_name":     tool_name,
        "input_hash":    input_hash or "",
        "output_hash":   output_hash or "",
        "duration_ms":   duration_ms or 0,
        "status":        status,
    }])

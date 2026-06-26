"""
BigQuery — primary destination for all agent-generated data.

Cloud SQL Postgres keeps: config_registry, authority_gradient_versions,
  configured_swarm_instances (startup config + RLS state machines),
  pending_actions, escalation_queue (human-approval workflows),
  agent_memory (pgvector semantic search),
  ticket_interactions (per-ticket decision gate reads).

BigQuery keeps: everything the agent produces.

Dataset: isrds_pmo
Tables:
  operating_briefs   — full Operating Brief text per cycle
  cycle_metrics      — per-cycle KPIs (stall count, hygiene score, etc.)
  trust_events       — every governance gate, decision, escalation
  stalled_tickets    — structured list of stalled tickets per scan
  hygiene_findings   — structured hygiene violations per scan
  raci_gaps          — structured RACI gaps per scan
  feature_snapshot   — feature build % per division per scan
  inter_agent_trace  — source→target agent calls with payload + latency
  tool_call_audit    — per-tool invocation log (name, status, duration)
"""
import logging
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)

_DATASET = "isrds_pmo"
_bq_client = None


def _client():
    global _bq_client
    if _bq_client is None:
        try:
            from google.cloud import bigquery
            from .config_registry import get_gcp_project
            _bq_client = bigquery.Client(project=get_gcp_project())
        except Exception as e:
            log.warning(f"BigQuery: client init failed ({e}) — analytics writes disabled")
            _bq_client = False
    return _bq_client if _bq_client else None


def _table(name: str) -> str:
    try:
        from .config_registry import get_gcp_project
        proj = get_gcp_project()
    except Exception:
        import os
        proj = os.environ.get("GOOGLE_CLOUD_PROJECT", "isr-division-systems-488723")
    return f"{proj}.{_DATASET}.{name}"


def _tenant() -> str:
    try:
        from .config_registry import get_tenant_id
        return get_tenant_id()
    except Exception:
        import os
        return os.environ.get("TENANT_ID", "ashs")


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


# ── operating_briefs ──────────────────────────────────────────────────────────

async def log_operating_brief(
    cycle_ts: datetime,
    mode: str,
    brief_text: str,
    stall_count: int = 0,
    gates_triggered: int = 0,
    duration_ms: float = 0.0,
) -> None:
    """Write the full Operating Brief to BigQuery. This is the system of record."""
    _insert("operating_briefs", [{
        "cycle_ts":       cycle_ts.isoformat(),
        "tenant_id":      _tenant(),
        "mode":           mode,
        "brief_text":     brief_text[:100000],
        "stall_count":    stall_count,
        "gates_triggered": gates_triggered,
        "duration_ms":    duration_ms,
        "inserted_at":    datetime.now(timezone.utc).isoformat(),
    }])


# ── cycle_metrics ─────────────────────────────────────────────────────────────

async def log_cycle_metrics(
    cycle_ts: datetime,
    mode: str,
    projects: list,
    duration_ms: float,
    stall_count: int = 0,
    hygiene_score: float = 0.0,
    raci_gap_count: int = 0,
    feature_pct_built: float = 0.0,
    gates_triggered: int = 0,
    errors: int = 0,
) -> None:
    _insert("cycle_metrics", [{
        "cycle_ts":          cycle_ts.isoformat(),
        "tenant_id":         _tenant(),
        "mode":              mode,
        "projects":          ",".join(projects),
        "duration_ms":       duration_ms,
        "stall_count":       stall_count,
        "hygiene_score":     hygiene_score,
        "raci_gap_count":    raci_gap_count,
        "feature_pct_built": feature_pct_built,
        "gates_triggered":   gates_triggered,
        "errors":            errors,
        "inserted_at":       datetime.now(timezone.utc).isoformat(),
    }])


# ── trust_events ──────────────────────────────────────────────────────────────

async def log_trust_event(
    event_type: str,
    detail: str,
    agent_id: str = "pmo_swarm",
    outcome: Optional[str] = None,
) -> None:
    _insert("trust_events", [{
        "event_ts":   datetime.now(timezone.utc).isoformat(),
        "tenant_id":  _tenant(),
        "swarm_id":   "pmo-swarm",
        "agent_id":   agent_id,
        "event_type": event_type,
        "detail":     str(detail)[:1000],
        "outcome":    outcome or "NEUTRAL",
    }])


# ── stalled_tickets ───────────────────────────────────────────────────────────

async def log_stalled_tickets(
    cycle_ts: datetime,
    tickets: list[dict],
) -> None:
    """
    tickets: list of {
        key, summary, project, assignee, assignee_email,
        status, priority, stall_hours, last_activity_at
    }
    """
    if not tickets:
        return
    now = datetime.now(timezone.utc).isoformat()
    tenant = _tenant()
    rows = [
        {
            "cycle_ts":         cycle_ts.isoformat(),
            "tenant_id":        tenant,
            "ticket_key":       t.get("key", ""),
            "summary":          str(t.get("summary", ""))[:500],
            "project":          t.get("project", ""),
            "assignee":         t.get("assignee", ""),
            "assignee_email":   t.get("assignee_email", ""),
            "status":           t.get("status", ""),
            "priority":         t.get("priority", ""),
            "stall_hours":      float(t.get("stall_hours", 0)),
            "last_activity_at": t.get("last_activity_at", ""),
            "inserted_at":      now,
        }
        for t in tickets
    ]
    _insert("stalled_tickets", rows)
    log.info(f"BigQuery: {len(rows)} stalled tickets written for {cycle_ts.date()}")


# ── hygiene_findings ──────────────────────────────────────────────────────────

async def log_hygiene_findings(
    cycle_ts: datetime,
    findings: list[dict],
) -> None:
    """
    findings: list of {
        key, project, violation_type, severity, field_missing, description
    }
    """
    if not findings:
        return
    now = datetime.now(timezone.utc).isoformat()
    tenant = _tenant()
    rows = [
        {
            "cycle_ts":       cycle_ts.isoformat(),
            "tenant_id":      tenant,
            "ticket_key":     f.get("key", ""),
            "project":        f.get("project", ""),
            "violation_type": f.get("violation_type", ""),
            "severity":       f.get("severity", "MEDIUM"),
            "field_missing":  f.get("field_missing", ""),
            "description":    str(f.get("description", ""))[:500],
            "inserted_at":    now,
        }
        for f in findings
    ]
    _insert("hygiene_findings", rows)
    log.info(f"BigQuery: {len(rows)} hygiene findings written for {cycle_ts.date()}")


# ── raci_gaps ─────────────────────────────────────────────────────────────────

async def log_raci_gaps(
    cycle_ts: datetime,
    gaps: list[dict],
) -> None:
    """
    gaps: list of {
        key, project, missing_role, current_assignee, summary
    }
    """
    if not gaps:
        return
    now = datetime.now(timezone.utc).isoformat()
    tenant = _tenant()
    rows = [
        {
            "cycle_ts":        cycle_ts.isoformat(),
            "tenant_id":       tenant,
            "ticket_key":      g.get("key", ""),
            "project":         g.get("project", ""),
            "missing_role":    g.get("missing_role", ""),
            "current_assignee": g.get("current_assignee", ""),
            "summary":         str(g.get("summary", ""))[:300],
            "inserted_at":     now,
        }
        for g in gaps
    ]
    _insert("raci_gaps", rows)
    log.info(f"BigQuery: {len(rows)} RACI gaps written for {cycle_ts.date()}")


# ── feature_snapshot ──────────────────────────────────────────────────────────

async def log_feature_snapshot(
    cycle_ts: datetime,
    snapshots: list[dict],
) -> None:
    """
    snapshots: list of {
        division, dept, sub_dept, total_features, built_features,
        pct_built, unbuilt_feature_names
    }
    """
    if not snapshots:
        return
    now = datetime.now(timezone.utc).isoformat()
    tenant = _tenant()
    rows = [
        {
            "cycle_ts":             cycle_ts.isoformat(),
            "tenant_id":            tenant,
            "division":             s.get("division", ""),
            "dept":                 s.get("dept", ""),
            "sub_dept":             s.get("sub_dept", ""),
            "total_features":       int(s.get("total_features", 0)),
            "built_features":       int(s.get("built_features", 0)),
            "pct_built":            float(s.get("pct_built", 0.0)),
            "unbuilt_feature_names": str(s.get("unbuilt_feature_names", ""))[:1000],
            "inserted_at":          now,
        }
        for s in snapshots
    ]
    _insert("feature_snapshot", rows)
    log.info(f"BigQuery: {len(rows)} feature snapshot rows written for {cycle_ts.date()}")


# ── inter_agent_trace ─────────────────────────────────────────────────────────

async def log_inter_agent_trace(
    trace_id: str,
    source_role: str,
    target_role: str,
    message_type: str,
    payload: Optional[dict] = None,
    latency_ms: Optional[int] = None,
    parent_trace_id: Optional[str] = None,
) -> None:
    """Record a source→target agent call. Replaces the dropped Postgres inter_agent_trace table."""
    import json as _json
    _insert("inter_agent_trace", [{
        "event_ts":        datetime.now(timezone.utc).isoformat(),
        "tenant_id":       _tenant(),
        "trace_id":        trace_id,
        "parent_trace_id": parent_trace_id or "",
        "source_role":     source_role,
        "target_role":     target_role,
        "message_type":    message_type,
        "payload":         _json.dumps(payload or {})[:2000],
        "latency_ms":      latency_ms or 0,
    }])


# ── tool_call_audit ───────────────────────────────────────────────────────────

async def log_tool_call_audit(
    trace_id: str,
    role_category: str,
    tool_name: str,
    status: str,
    duration_ms: Optional[int] = None,
    input_hash: Optional[str] = None,
    output_hash: Optional[str] = None,
) -> None:
    """Record a single tool invocation. Replaces the dropped Postgres tool_call_audit table.
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


# ── Schema bootstrap ──────────────────────────────────────────────────────────

def ensure_dataset_and_tables() -> None:
    """Create isrds_pmo dataset and all tables if they don't exist.
    Called once at daemon startup.
    """
    bq = _client()
    if bq is None:
        return

    from google.cloud import bigquery

    try:
        from .config_registry import get_gcp_project
        project = get_gcp_project()
    except Exception:
        import os
        project = os.environ.get("GOOGLE_CLOUD_PROJECT", "isr-division-systems-488723")

    ds_ref = bigquery.DatasetReference(project, _DATASET)
    try:
        bq.get_dataset(ds_ref)
    except Exception:
        ds = bigquery.Dataset(ds_ref)
        ds.location = "US"
        ds.description = "ISRDS PMO Swarm — all agent-generated data"
        bq.create_dataset(ds, exists_ok=True)
        log.info(f"BigQuery: dataset {project}.{_DATASET} created")

    TABLES = {
        "operating_briefs": {
            "fields": [
                ("cycle_ts",        "TIMESTAMP", "REQUIRED"),
                ("tenant_id",       "STRING",    "REQUIRED"),
                ("mode",            "STRING",    "NULLABLE"),
                ("brief_text",      "STRING",    "NULLABLE"),
                ("stall_count",     "INTEGER",   "NULLABLE"),
                ("gates_triggered", "INTEGER",   "NULLABLE"),
                ("duration_ms",     "FLOAT",     "NULLABLE"),
                ("inserted_at",     "TIMESTAMP", "NULLABLE"),
            ],
            "partition": "cycle_ts",
            "description": "Full Operating Brief text per cycle. System of record.",
        },
        "cycle_metrics": {
            "fields": [
                ("cycle_ts",          "TIMESTAMP", "REQUIRED"),
                ("tenant_id",         "STRING",    "REQUIRED"),
                ("mode",              "STRING",    "NULLABLE"),
                ("projects",          "STRING",    "NULLABLE"),
                ("duration_ms",       "FLOAT",     "NULLABLE"),
                ("stall_count",       "INTEGER",   "NULLABLE"),
                ("hygiene_score",     "FLOAT",     "NULLABLE"),
                ("raci_gap_count",    "INTEGER",   "NULLABLE"),
                ("feature_pct_built", "FLOAT",     "NULLABLE"),
                ("gates_triggered",   "INTEGER",   "NULLABLE"),
                ("errors",            "INTEGER",   "NULLABLE"),
                ("inserted_at",       "TIMESTAMP", "NULLABLE"),
            ],
            "partition": "cycle_ts",
            "description": "Per-cycle KPIs. Powers trend dashboards in Looker Studio.",
        },
        "trust_events": {
            "fields": [
                ("event_ts",   "TIMESTAMP", "REQUIRED"),
                ("tenant_id",  "STRING",    "REQUIRED"),
                ("swarm_id",   "STRING",    "NULLABLE"),
                ("agent_id",   "STRING",    "NULLABLE"),
                ("event_type", "STRING",    "NULLABLE"),
                ("detail",     "STRING",    "NULLABLE"),
                ("outcome",    "STRING",    "NULLABLE"),
            ],
            "partition": "event_ts",
            "description": "Every governance gate, decision, and escalation.",
        },
        "stalled_tickets": {
            "fields": [
                ("cycle_ts",        "TIMESTAMP", "REQUIRED"),
                ("tenant_id",       "STRING",    "REQUIRED"),
                ("ticket_key",      "STRING",    "NULLABLE"),
                ("summary",         "STRING",    "NULLABLE"),
                ("project",         "STRING",    "NULLABLE"),
                ("assignee",        "STRING",    "NULLABLE"),
                ("assignee_email",  "STRING",    "NULLABLE"),
                ("status",          "STRING",    "NULLABLE"),
                ("priority",        "STRING",    "NULLABLE"),
                ("stall_hours",     "FLOAT",     "NULLABLE"),
                ("last_activity_at","STRING",    "NULLABLE"),
                ("inserted_at",     "TIMESTAMP", "NULLABLE"),
            ],
            "partition": "cycle_ts",
            "description": "Structured list of stalled tickets per scan.",
        },
        "hygiene_findings": {
            "fields": [
                ("cycle_ts",       "TIMESTAMP", "REQUIRED"),
                ("tenant_id",      "STRING",    "REQUIRED"),
                ("ticket_key",     "STRING",    "NULLABLE"),
                ("project",        "STRING",    "NULLABLE"),
                ("violation_type", "STRING",    "NULLABLE"),
                ("severity",       "STRING",    "NULLABLE"),
                ("field_missing",  "STRING",    "NULLABLE"),
                ("description",    "STRING",    "NULLABLE"),
                ("inserted_at",    "TIMESTAMP", "NULLABLE"),
            ],
            "partition": "cycle_ts",
            "description": "Structured hygiene violations per scan.",
        },
        "raci_gaps": {
            "fields": [
                ("cycle_ts",         "TIMESTAMP", "REQUIRED"),
                ("tenant_id",        "STRING",    "REQUIRED"),
                ("ticket_key",       "STRING",    "NULLABLE"),
                ("project",          "STRING",    "NULLABLE"),
                ("missing_role",     "STRING",    "NULLABLE"),
                ("current_assignee", "STRING",    "NULLABLE"),
                ("summary",          "STRING",    "NULLABLE"),
                ("inserted_at",      "TIMESTAMP", "NULLABLE"),
            ],
            "partition": "cycle_ts",
            "description": "Tickets with missing accountable or responsible owners.",
        },
        "feature_snapshot": {
            "fields": [
                ("cycle_ts",              "TIMESTAMP", "REQUIRED"),
                ("tenant_id",             "STRING",    "REQUIRED"),
                ("division",              "STRING",    "NULLABLE"),
                ("dept",                  "STRING",    "NULLABLE"),
                ("sub_dept",              "STRING",    "NULLABLE"),
                ("total_features",        "INTEGER",   "NULLABLE"),
                ("built_features",        "INTEGER",   "NULLABLE"),
                ("pct_built",             "FLOAT",     "NULLABLE"),
                ("unbuilt_feature_names", "STRING",    "NULLABLE"),
                ("inserted_at",           "TIMESTAMP", "NULLABLE"),
            ],
            "partition": "cycle_ts",
            "description": "Feature build % per division per scan.",
        },
        "inter_agent_trace": {
            "fields": [
                ("event_ts",        "TIMESTAMP", "REQUIRED"),
                ("tenant_id",       "STRING",    "REQUIRED"),
                ("trace_id",        "STRING",    "NULLABLE"),
                ("parent_trace_id", "STRING",    "NULLABLE"),
                ("source_role",     "STRING",    "NULLABLE"),
                ("target_role",     "STRING",    "NULLABLE"),
                ("message_type",    "STRING",    "NULLABLE"),
                ("payload",         "STRING",    "NULLABLE"),
                ("latency_ms",      "INTEGER",   "NULLABLE"),
            ],
            "partition": "event_ts",
            "description": "Source→target agent calls with payload and latency. Moved from Postgres.",
        },
        "tool_call_audit": {
            "fields": [
                ("event_ts",      "TIMESTAMP", "REQUIRED"),
                ("tenant_id",     "STRING",    "REQUIRED"),
                ("trace_id",      "STRING",    "NULLABLE"),
                ("role_category", "STRING",    "NULLABLE"),
                ("tool_name",     "STRING",    "NULLABLE"),
                ("input_hash",    "STRING",    "NULLABLE"),
                ("output_hash",   "STRING",    "NULLABLE"),
                ("duration_ms",   "INTEGER",   "NULLABLE"),
                ("status",        "STRING",    "NULLABLE"),
            ],
            "partition": "event_ts",
            "description": "Per-tool invocation log (name, status, duration). Moved from Postgres.",
        },
    }

    for table_name, spec in TABLES.items():
        _ensure_table(bq, project, table_name, spec)

    log.info(f"BigQuery: {_DATASET} — {len(TABLES)} tables ready")


def _ensure_table(bq, project: str, table_name: str, spec: dict) -> None:
    from google.cloud import bigquery
    ref = bigquery.TableReference(bigquery.DatasetReference(project, _DATASET), table_name)
    try:
        bq.get_table(ref)
        return
    except Exception:
        pass
    schema = [
        bigquery.SchemaField(name, ftype, mode=mode)
        for name, ftype, mode in spec["fields"]
    ]
    table = bigquery.Table(ref, schema=schema)
    table.description = spec.get("description", "")
    table.time_partitioning = bigquery.TimePartitioning(
        type_=bigquery.TimePartitioningType.DAY,
        field=spec["partition"],
    )
    bq.create_table(table, exists_ok=True)
    log.info(f"BigQuery: table {_DATASET}.{table_name} created")

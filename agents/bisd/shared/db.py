"""
BISD shared database stub.
Data routing:
  PostgreSQL  — operational/relational (config_registry, pending_actions, escalation_queue)
  BigQuery    — all agent-generated analytical data (isrds_bisd dataset)
  Firestore   — live activity state per agent
"""


def get_postgres_conn():
    """STUB — returns Cloud SQL Postgres connection."""
    raise NotImplementedError("STUB")


def get_bigquery_client():
    """STUB — returns BigQuery client for isrds_bisd dataset."""
    raise NotImplementedError("STUB")


def get_firestore_client():
    """STUB — returns Firestore client."""
    raise NotImplementedError("STUB")

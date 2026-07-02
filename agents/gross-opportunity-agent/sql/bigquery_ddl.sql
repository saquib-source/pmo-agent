-- Gross Opportunity Agent — BigQuery DDL
-- Dataset: goa (or one dataset with three table families — Calibrated)
-- Apply once per GCP project. Gap: project id and dataset location.

CREATE SCHEMA IF NOT EXISTS goa;

-- Raw landing. One row per pull, payload kept verbatim.
CREATE TABLE IF NOT EXISTS goa.raw_opportunity (
  raw_id           STRING NOT NULL,
  source_id        STRING NOT NULL,
  source_record_id STRING,
  payload          JSON NOT NULL,
  pull_mode        STRING,            -- backfill or delta
  ingested_at      TIMESTAMP NOT NULL
);

-- Normalized to the canonical shape, before dedup.
CREATE TABLE IF NOT EXISTS goa.normalized_opportunity (
  normalized_id        STRING NOT NULL,
  raw_id               STRING NOT NULL,
  source_id            STRING NOT NULL,
  project_identity_key STRING NOT NULL,
  project_name         STRING,
  record_type          STRING,        -- active_bid, itb, permit_signal, owner_pipeline, planning_signal
  stage                STRING,
  street               STRING,
  city                 STRING,
  state                STRING,
  postal_code          STRING,
  country              STRING,
  owner                STRING,
  valuation            NUMERIC,
  bid_date             DATE,
  csi_divisions        ARRAY<STRING>,
  primary_source_url   STRING,
  normalized_at        TIMESTAMP NOT NULL
);

-- Deduplicated analytics copy of the gross bucket.
-- The screen does not read BigQuery directly; this is for analytics.
CREATE TABLE IF NOT EXISTS goa.gross_opportunity (
  opportunity_id       STRING NOT NULL,
  project_identity_key STRING NOT NULL,
  project_name         STRING,
  record_type          STRING,
  stage                STRING,
  status               STRING,        -- active, closed, rejected
  city                 STRING,
  state                STRING,
  valuation            NUMERIC,
  bid_date             DATE,
  csi_divisions        ARRAY<STRING>,
  gate_passed          BOOL,
  gate_score           FLOAT64,
  first_seen_at        TIMESTAMP,
  last_changed_at      TIMESTAMP
);

# Gross Opportunity Agent — Implementation Plan

**For:** Saquib (implementation), Manmeet (runtime + prerequisites)
**Tenant:** Basco · Projects division · Pillar 1
**Bound to:** Data Contract v1.0 (frozen)
**Status:** Scaffold committed on `feat/gross-opportunity-agent`. This document is the build guide from scaffold to a working thin slice.

---

## 1. What we are building, in one paragraph

A single ingestion-and-discovery agent that reads commercial construction opportunity
sources, lands them in a data lake, normalizes them to one shape, removes duplicates,
applies a loose relevance screen, and serves a ranked human review list. **It never bids
and never contacts anyone** — that is later pillars. It is *one orchestrator over six
internal functions*, not a multi-agent swarm.

There are two surfaces:

1. **The agent** — the backend pipeline (this repo). Runs on a schedule, fills the serving store.
2. **The review screen** — an HTML app the reviewer (Todd) uses to work the queue. It never
   touches the database directly; it calls the agent's **Events API**, which is bound to the
   same Data Contract v1.0 keys the agent writes.

**Review-screen prototype (authoritative design reference):**
https://claude.ai/code/artifact/6e272157-4a4d-45c6-a238-859dd7772b5f

The prototype is bound to the exact contract keys, has a Show-Keys toggle that overlays each
key on the element it drives, and exposes `window.__screen` for automated testing. Build the
production screen against this reference; the contract governs over labels.

---

## 2. The six functions (single agent, internal composition)

| # | Function | Engine role | Determinism |
|---|----------|-------------|-------------|
| 1 | **Connector adapters** — pull raw records, one adapter per method | none | deterministic |
| 2 | **Normalizer** — map raw → canonical shape | `normalizer_extraction` | deterministic + light model for messy fields |
| 3 | **Dedup engine** — collapse same-project records, keep all links | `dedup_ambiguous_merge` | keys + fuzzy + embeddings + model only for the ambiguous middle |
| 4 | **Coarse relevance gate** — apply screening rules + scope, recall-first | `gate_classifier` | rules + small classifier for the ambiguous middle |
| 5 | **Source discovery scout** — find new sources, propose for human APPROVE | `scout_reasoning` | reasoning + web search, runs weekly |
| 6 | **Liveness watchdog + fork router** — silence detection, route non-opportunities out | none | deterministic |

**Runtime engine binding.** No vendor/model string appears in any function. Each role resolves
at runtime through the Config Registry (`config/engines.json`). Per Manmeet's confirmed
decision (2026-07-01), **all four roles resolve to `claude-fable-5`**, overriding the build
spec's default haiku/sonnet calibration. To change engines, edit `config/engines.json` only —
never the function code.

---

## 3. Four data stores

| Store | Tech | Role | Who reads/writes |
|-------|------|------|------------------|
| **Lake** | BigQuery | raw → normalized → deduplicated analytics copy | agent writes; analytics reads |
| **Serving** | Cloud SQL (PostgreSQL 15) | live opportunity record, seen events, rejections, rules, scope, source registry, watermarks, idempotency markers | agent writes; Events API reads/writes; screen reads via API |
| **Activity stream** | Firestore | append-only ticker feed | agent writes; screen subscribes |
| **Agent memory** | Vertex AI Agent Engine | scout inter-run context (light use) | agent |

The screen **never** reads BigQuery and **never** writes the database directly. It only calls the Events API.

---

## 4. What is already scaffolded (this branch)

```
agents/gross-opportunity-agent/
  config/
    engines.json                # Config Registry — 4 roles → claude-fable-5
    sources/sam_gov.json        # worked-example source config (Gaps marked)
    sources/_template.json      # template for new sources
  sql/
    bigquery_ddl.sql            # 3 lake tables
    cloudsql_ddl.sql            # 10 serving tables
  goa/
    schemas/canonical.py        # CanonicalOpportunity dataclass
    stores/                     # config_registry, bigquery, cloudsql, firestore, memory
    adapters/                   # base, rest, sam_gov, alert_email, inbound_push
    normalize/                  # normalizer, address, csi
    dedup/                      # keying, match, embed, merge, idempotency (atomic commit)
    gate/                       # rules (Section 10 semantics), classifier
    events/                     # api.py (8 contract events), full_report.py (4-state job)
    watchdog/                   # liveness, fork_router
    observability/              # tracing, metrics, critical_state
    scout/                      # scout (behind APPROVE gate)
    orchestrator.py             # root pipeline coordinator
  jobs/                         # backfill, delta, expiration_sweep
  tests/                        # unit + integration (empty, to be written)
  agent-spec.yaml prompt.md tool-registry.json memory-schema.json governance-rules.yaml workflow.py
  pyproject.toml
```

**Scaffold posture:** the structure, contracts, DDL, deterministic logic (keying, rule engine,
idempotency transaction, events, state machine), and the Config Registry are real and complete.
The **model calls are stubs** — they log the resolved engine and return a safe recall-first
default until wired to the ADK runtime. Every unknown value from the spec is left `null` with a
`Gap:` comment; none are invented.

---

## 5. How the agent works, end to end

### 5.1 Per-record pipeline (`goa/orchestrator.py::run_source`)

```
for raw in adapter.pull(mode, watermark):
    hash = idempotency.stable_hash(source_id, raw)
    if mode == delta and already_fired(hash): continue
    norm   = normalize(raw, source_cfg)          # function 2
    opp    = dedup.upsert(norm)                   # function 3 (exact key → blocking → fuzzy → embed → model)
    gate   = gate.evaluate(opp) + classifier      # function 4 (exclude → include → scope → classifier, recall-first)
    commit_record(source_id, hash, opp):          # ATOMIC (function-8 idempotency)
        if fired_marker exists: return            # never double-write
        upsert opportunity + source_links
        upsert BigQuery gross copy
        insert fired_marker
    firestore.activity(gated_kept | gated_dropped)
advance_watermark(source_id)
fork_router.route_non_opportunities(source_id)    # function 6
```

The **fired_marker** table's composite primary key is the correctness guarantee: a second
concurrent writer for the same source record fails the insert and rolls back rather than
creating a duplicate. Overlapping sources that carry the same project still collapse to one
opportunity via the **project identity key** (SHA-256 of normalized address + owner + project
name + valuation bucket + bid date).

### 5.2 Run modes (`jobs/`)

- **Backfill** — one heavy pass when a source is first enabled. Run once, manually.
- **Delta** — scheduled incremental pass; cursor/watermark where the source supports it, else
  re-scan and compare by record hash. A record now absent or past bid date → `status = closed`.
- **Expiration sweep** — daily; closes any active record past `bid_date` with `closed_reason = expired`.

### 5.3 Recall-first

When gate rules and classifier disagree, or confidence is low, **keep** the record. Dropping is
reserved for a clear exclude match (past bid date, single-family residential only). Deep
qualification is the Pre-Bidder's job (Pillar 2), not this agent's.

---

## 6. The review screen ↔ agent contract (Events API)

The screen calls these endpoints (`goa/events/api.py`); each maps to a Data Contract v1.0 event:

| Endpoint | Effect |
|----------|--------|
| `list_opportunities(user_id, status)` | ranked rows, `seen_state` resolved per user |
| `get_counts(user_id)` | per-user `new`/`seen` + shared `total_active`/`rejected`/`closed` |
| `open_detail(opportunity_id)` | detail-surface fields already in the store |
| `pull_full_report(opportunity_id)` | sets `fetch_state=pulling`, enqueues async job, returns immediately |
| `mark_seen(opportunity_id, user_id, via)` | inserts seen_event; `via` = scroll or opened; never changes status |
| `reject_opportunity(...)` | sets status rejected; if `rule_scope=permanent`, writes a rule to `rule_target` (default `initial_screening`) |
| `edit_criteria(list, operation, rule_or_value)` | add/toggle/remove a rule or update scope; takes effect on next gate eval |
| `reopen_opportunity(opportunity_id)` | status back to active |

**Fetch state machine** (drives the full-report button): `summary → pulling → full_pulled | failed`.
The async job (`goa/events/full_report.py`) fetches the full source record on demand and stores
it as flexible source-shaped JSON; the screen renders it generically.

**Per-user vs shared state:** `new_count`, `seen_count`, `seen_by` are per-user; `total_active`,
`rejected_count`, `closed_count` are shared. Counts are computed by a query, not stored.

---

## 7. Build order for Saquib (maps to build-spec Section 19)

Legend: **[H]** human prerequisite · **[✓]** scaffolded, needs wiring/verification · **[ ]** to build

1. **[H]** Provision the 4 stores, apply `sql/bigquery_ddl.sql` and `sql/cloudsql_ddl.sql`. *(Manmeet/Mignonne)*
2. **[H]** Load seeded scope + `initial_screening` rules from Data Contract v1.0 into Cloud SQL. *(Manmeet)*
3. **[✓]** Stores, schemas, Config Registry — scaffolded. **Wire:** set `CLOUDSQL_DSN`, `GOOGLE_CLOUD_PROJECT`, region; confirm `asyncpg` connects; confirm Config Registry loads `engines.json`.
4. **[✓/ ]** Adapter interface + SAM.gov adapter — scaffolded. **Do:** resolve SAM.gov Gaps (NAICS codes, keyword set, pagination), store API key in Secret Manager, verify a live query returns and maps.
5. **[✓/ ]** Normalizer → dedup → coarse gate — scaffolded. **Wire:** the three model calls (`normalizer_extraction`, `dedup_ambiguous_merge`, `gate_classifier`) to the ADK runtime; review the CSI extraction dictionary against scope divisions.
6. **[✓/ ]** Orchestrator + backfill/delta jobs — scaffolded. **Do:** run SAM.gov backfill, then delta; confirm records land, de-duplicate, screen, and that a re-run creates no duplicates.
7. **[✓/ ]** Events API + async full-report job — scaffolded. **Do:** put behind an HTTP entrypoint (FastAPI/Cloud Run), replace the in-process queue with Cloud Tasks, point the screen at it.
8. **[✓]** Watchdog, fork router, activity stream — scaffolded. **Do:** wire the escalation channel; wire competitor/FF&E fork targets when those stores exist.
9. **[✓/ ]** Observability, cost metrics, counts query — scaffolded. **Do:** create Cloud Monitoring metric descriptors; confirm per-role model-cost tagging once model calls are live.
10. **[✓/ ]** Scout — scaffolded behind APPROVE gate. **Wire:** the reasoning+web-search model call; confirm proposals land with `enabled=false`.
11. **[ ]** Acceptance test with the screen bound to the same contract (see §10).

---

## 8. Gaps to resolve (do not invent — surface and fill with the owner)

| Gap | Owner |
|-----|-------|
| GCP project id + region | Manmeet |
| SAM.gov NAICS codes + keyword set + pagination for scope | Manmeet + source registry |
| Verified source registry + confirmed per-source costs | Meri (BAS-5) |
| Per-source cadence | source registry |
| Real recall / duplication / cost targets | Manmeet |
| Critical-state thresholds | after real volumes exist |
| CSI extraction dictionary review | product team |
| Authoritative deep_criteria (screen shows placeholders) | Derek + Shawn (Pillar 2) |
| Source credentials in Secret Manager | Tayyab |

---

## 9. Deployment

- **One service account**, least-privilege: read/write BigQuery datasets, read/write Cloud SQL,
  read/write the Firestore collection, read named secrets, invoke model endpoints.
- **Agent + Events API** deploy to Vertex AI Agent Engine (or Cloud Run if a container is
  preferred — Manmeet's runtime call).
- **Cloud Scheduler**: one delta job per source + the daily expiration sweep + weekly scout +
  periodic liveness check. Backfill is run once, manually, per source.
- **Cloud SQL**: PostgreSQL 15, private where possible, reached through the standard connector.
- **Secrets**: every credential in Secret Manager, referenced by name from the source config.
  Nothing secret in the repo or in a ticket.

---

## 10. Testing and acceptance

- **Unit:** adapter paging + watermark; normalizer field map + address + CSI; dedup keying +
  merge + idempotency under concurrent writes; gate rule evaluation incl. recall-first; fork routing.
- **Integration:** one live free source (SAM.gov first — fastest credential). Backfill, then
  delta; confirm records land, de-duplicate, screen, and that a re-run creates zero duplicates.
- **Acceptance (the demo bar):** one or two free sources land end to end, de-duplicated and
  screened, served as a list ranked by `gate_score`, with a source link per row, working
  detail-on-click that pulls the full report on demand, per-user new/seen, and a working reject
  with reason. Recall/duplication/cost thresholds are Gap until real targets are set — acceptance
  for the thin slice is **behavioral, not numeric**.

---

## 11. Guardrails (must hold; enforced in code + review)

- **No external write** anywhere. The agent reads, stores, screens, forks. It contacts no one.
- **No bid, no contact.** Out of scope by design.
- **Vendor-neutral.** No model/vendor string in function code; engines resolve via Config Registry.
- **Recall-first.** Uncertain → keep.
- **Idempotency.** Every commit is atomic with the fired_marker insert.
- **Data minimization.** No personal contact or banking detail in the lake; the on-demand full
  report may hold more and is fetched only when a human asks.
- **Terms compliance.** Where a portal prohibits scraping, use the alert-email path, not a scraper.

---

## 12. Ownership summary (from the Human Runbook)

| Step | Owner |
|------|-------|
| Provision stores, apply DDL | Manmeet / Mignonne |
| Load seed scope + rules | Manmeet |
| Source credentials → Secret Manager | Tayyab |
| Confirm runtime, deploy, Scheduler jobs | Manmeet |
| Approve SAM.gov as first source (APPROVE gate) | Todd or delegate |
| Run SAM.gov backfill, confirm delta | Manmeet |
| Open review screen, set CSI divisions, adjust rules | Todd |
| Acceptance test | Todd + Manmeet |
| Implementation (functions, wiring, deploy) | **Saquib** |

---

*Bound to Data Contract v1.0. Engine binding per Config Registry (`config/engines.json`), all
roles `claude-fable-5` per Manmeet 2026-07-01. This plan tracks build-spec v1.0 Section 19.*

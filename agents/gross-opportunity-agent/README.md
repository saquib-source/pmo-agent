# Gross Opportunity Agent (GOA)

A swarm of six agents that finds, normalizes, de-duplicates, and screens construction
RFPs (shower/toilet partitions, CSI 10/08/22 scope) from SAM.gov into a reviewer
console. Everything is real: real SAM.gov API, real Cloud SQL serving store, real
BigQuery lake, real model calls resolved through the vendor-neutral Config Registry.

## The swarm

| Agent | Does | Engine role (engines.json) |
|---|---|---|
| Root Orchestrator | runs the pipeline, owns the request budget + watermark | — |
| Source Connector | pulls SAM.gov (keyword/NAICS query plan), full-report fetches | — |
| Normalizer | raw record → canonical schema, CSI detection | `normalizer_extraction` |
| Dedup Agent | identity key → fuzzy → embedding → model arbitration | `dedup_ambiguous_merge` |
| Screening Gate | deterministic rules + model classifier, recall-first | `gate_classifier` |
| Committer | atomic idempotent writes (Cloud SQL + BigQuery + fired_marker) | — |
| Watchdog | daily expiration sweep | — |
| Scout | weekly new-source discovery | `scout_reasoning` |

No agent code names a vendor or model. Bindings live in `config/engines.json`
(production: vertex transport) and can be overridden per-environment with
`GOA_ENGINE_TRANSPORT` / `GOA_ENGINE_OVERRIDE_VERSION` — still config, never code.

## API request budget (why we never see a 429)

SAM.gov free personal keys get **10 requests/day** (resets 00:00 UTC). Every search
page and every full-report fetch is one request. GOA enforces this *client-side*:

- `config/sources/sam_gov.json → rate_limit.requests_per_day: 10` is the quota.
  `reserve_for_ui: 2` is held back so reviewers can always pull full reports.
- Usage persists in Cloud SQL (`api_request_ledger`, keyed on UTC day), shared by
  scheduled runs and console full-pulls, and survives restarts.
- The orchestrator computes `budget = quota − reserve − used_today` before each run
  and hands it to the adapter, which **stops before the request that would exceed
  it** (`BudgetExhausted`). The watermark then stays put, so tomorrow's run resumes
  the same window. A source 429 is still handled as a backstop, but should never fire.
- The query plan makes the budget go far: `limit=1000` + one search per keyword in
  `query_plan.keywords` ⇒ a full daily delta ≈ 3 requests.

**When the quota upgrade lands (1,000/day):** either edit `requests_per_day` in
`sam_gov.json` and run `python -m jobs.seed`, **or** just set env
`GOA_REQUESTS_PER_DAY=1000` on the Cloud Run job (env wins, zero redeploy). Then
optionally widen `query_plan.keywords` / enable `query_plan.naics`.

## Do the same leads come back tomorrow?

Yes from SAM.gov, no in GOA — three dedup layers: `fired_marker` (exact record hash,
skipped before any model cost), `project_identity_key` (same project reposted →
merge), fuzzy/embedding/model arbitration (lookalikes → merge). Plus the watermark
means delta runs only ask SAM for notices posted since the last good run (with a
1-day overlap that dedup absorbs).

## Run schedule

- **Production:** Cloud Scheduler → Cloud Run job `goa-daily-delta`, **daily 00:30 UTC**
  (right after the SAM.gov quota reset, so the run always has a fresh budget).
- **Local/manual:** `python -m jobs.delta --source sam_gov` (or `jobs.backfill` once).

## Console

Cloud Run service `goa-console` (same image serves `screen/index.html` + Events API).

- **Review Queue** — RFPs ranked by gate score; full detail + agent decision trail.
- **Agent Swarm** — per-agent cards (live status, engine binding, counters, last
  action), the API request-budget meter, and a per-agent filtered log. Click a card.
- **Live Activity** — every pipeline step streaming, attributed to its agent.
- **Pipeline Stats** — funnel, dedup collapse, score distribution, breakdowns.

## Local run

```bash
cd agents/gross-opportunity-agent
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in; see comments in .env
set -a; . ./.env; set +a
.venv/bin/python -m jobs.seed                      # once: scope, rules, source registry
.venv/bin/python -m uvicorn goa.events.http_app:app --port 8080   # console at :8080
.venv/bin/python -m jobs.delta --source sam_gov    # one real pull (budget-guarded)
```

On this dev machine specifically: build the proxy CA bundle first
(`scripts/build_ca_bundle.sh`) and keep `GOA_FORCE_IPV4=1`, `GOA_SKIP_LAKE=1`,
`GOA_SKIP_ACTIVITY=1` (gRPC clients are proxy-blocked locally; all run fine on Cloud Run).

## Deploy (Cloud Run, project isr-division-systems-488723)

```bash
export CLOUDSDK_PYTHON=/opt/homebrew/bin/python3.12
gcloud config set core/custom_ca_certs_file <combined-ca-bundle.pem>   # proxy machines only

# Console service (build from source; .gcloudignore keeps config/*.json in the image)
gcloud run deploy goa-console --source . --region us-central1 --allow-unauthenticated \
  --memory 1Gi \
  --set-env-vars GOOGLE_CLOUD_PROJECT=isr-division-systems-488723,GOOGLE_CLOUD_LOCATION=us-central1,GOA_BQ_DATASET=goa,GOA_ENGINE_TRANSPORT=anthropic \
  --set-secrets CLOUDSQL_DSN=goa-cloudsql-dsn:latest,SAM_GOV_API_KEY=sam-gov-api-key:latest,ANTHROPIC_API_KEY=goa-anthropic-api-key:latest

# Daily job (same image) + scheduler at 00:30 UTC
gcloud run jobs create goa-daily-delta --image <console image> --region us-central1 \
  --command python --args=-m,jobs.delta,--source,sam_gov --task-timeout 3600 --max-retries 1 \
  --set-env-vars ... --set-secrets ...   # same as service
gcloud scheduler jobs create http goa-daily-delta-trigger --location us-central1 \
  --schedule "30 0 * * *" --time-zone Etc/UTC --http-method POST \
  --uri "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/isr-division-systems-488723/jobs/goa-daily-delta:run" \
  --oauth-service-account-email 1059272334202-compute@developer.gserviceaccount.com
```

Production requirement worth doing: register the SAM.gov entity role for the
**system-account key (1,000 req/day)** — free, ~2–3 weeks lead time.

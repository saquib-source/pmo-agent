# Data Provenance — BISD Operations Surface & Gross Opportunity Console

Complete developer documentation for **every visible value** on the two screens:

- **Surface A — BISD org chart** (`agents/bisd/org-chart/index.html`, 751 lines, single self-contained file, deployed to `gs://isrds-bisd-org-chart/index.html`)
- **Surface B — Gross Opportunity console** (`agents/gross-opportunity-agent/screen/index.html`, 550 lines, served by FastAPI at `/` on Cloud Run `goa-console`)

**The one-sentence version:** on Surface A every number is **simulated — deterministic per node name** (placeholders pending "9.1 and telemetry", exactly as the on-screen badge says) *except* the Gross Opportunity Agent Capability card, which pulls real values from the console API; on Surface B **every number is real** (Cloud SQL via the events API) and only labels/thresholds are hardcoded.

Legend used throughout: ✅ Real · 🟡 Mock/simulated · 🔴 Hardcoded · ⚪ Placeholder/empty-state.

---

## A. BISD org chart — master value table

### A1. Chrome (badge, breadcrumb, band, command bar)

| UI Element | Current value | Meaning | Type | Origin | Calculation / source field | Real? | Change at | Future backend |
|---|---|---|---|---|---|---|---|---|
| Badge title | "SIMULATED DATA · ATOM surface, dark instrument · … pending 9.1 and telemetry" | Honest label: metrics are placeholders | 🔴 Hardcoded | `index.html:186` | — | Label (true statement) | index.html:186 | remove when telemetry lands |
| Legend dots | working / standby / attention (`#39D6A0`/`#6E8091`/`#F0564B`) | Status color key | 🔴 Hardcoded | `index.html:187` | — | Label | same line | — |
| LIVE legend line | "LIVE Gross Opportunity console connected" (hidden by default) | Console reachability indicator | ✅ Real | `index.html:188` (`#liveleg`), shown by `refreshLive()` | displayed when `GET /api/counts` succeeds | Real signal | — | Data Contract v1.1 `status` field |
| Breadcrumb | e.g. "Basco Installed Sales Division › Demand Generation › Projects › …" | Path of the focused node | ✅ Real (structure) | `drawCrumb()` ≈`index.html:560`; names from `window.DATA` | `focus.ancestors()` | Real org structure | `naming_tree.txt` → `generate_data.py` | org-structure service |
| Bottom band (node stub) | "Gross Opportunity Agent Capability · output 7 per year · live from the console, 3 new for review" | One-line summary of focused node | Mixed | `drawCrumb()` band branch | `output {vol}/yr` or `achievement {live} of {target}`; "· live from the console, N new" appended when `VALUES[name]._live` (N = `/api/counts.new_count`) | 7 & 3 are ✅ real; target phrasing 🟡 sim | — | counts API (already) |
| Band hint | "double click a node to open its user screens here" | Usage hint | 🔴 Hardcoded | band template | — | Label | — | — |
| Command bar placeholder | "type where to go, e.g. take me to win the bid" | Navigation affordance | 🔴 Hardcoded | `index.html:199` | resolver: token match ≥0.8, aliases (`ALIASES` `index.html:645`), fallback ≥0.45 | Label; aliases 🔴 | `ALIASES` list | model resolver (toast already says so) |
| Toast messages | "going to …", "unresolved …", "voice is blocked in the viewer…" | Feedback | 🔴 Hardcoded | `handlePhrase()`/mic handler | — | Labels | — | — |
| Page title | "BISD Operations Surface" | Browser tab | 🔴 Hardcoded | `index.html:4` | — | Label | — | — |

### A2. Every node card (four widget sizes, all 133 nodes)

Widget sizes: minimized 152×54 → normal 256×140 → selected 340×252 → expanded 428×498 (`SZ`, `index.html:352`). Accent color by level: Subsidiary `#2D6CDF`, Division `#2E9E8F`, Department `#5C6B7E`, Sub-Department `#B06A2C`, Function `#7A5AB0` (`ACCENT`, `index.html:350`).

All simulated numbers use one PRNG: `seed(nodePath + "#tag")` → xorshift (`index.html:250-251`). **Deterministic**: the same node shows the same numbers on every load, on every machine. Real numbers enter through `window.VALUES` (`index.html:228`) and flow through the *same* roll-up code.

| UI Element | Example value | Meaning | Type | Origin | Calculation | Real? | Change at | Future backend |
|---|---|---|---|---|---|---|---|---|
| Node name | "Gross Opportunity Agent Capability" | Canonical capability name (BISD Business System Naming Tree) | ✅ Real | `window.DATA` `index.html:204` ← `naming_tree.txt` (via `generate_data.py`) | — | Real org names, no people names anywhere | edit `naming_tree.txt`, run `python3 generate_data.py` | org-structure service |
| Status light + label | Working / Standby / Attention | Run state of the capability | 🟡 Sim (✅ for GOA) | `runState()` `index.html:363` | non-VALUES nodes: seeded hash — 80% run, 13% idle, 7% fault. VALUES nodes: Standby until `_live` (console reachable) or `_activated` (tap-to-activate) → Working | Sim except GOA | `VALUES[name].status` | Data Contract v1.1 `status`, `last_active`, `activate` event |
| Outcome number "N / yr" | GOA: **7** / yr | Achieved output this year | 🟡 Sim (✅ GOA) | leaf: `baseVolume()` `index.html:284`; override `VALUES[name].live` | sim: `250 + seeded()*1700` → 250–1949. Parents: plain sum, or **pipeline flow** for Projects & function rows (see A4). GOA: `/api/counts.total_active` = 7 | GOA ✅, rest 🟡 | `VALUES[name].live` | per-capability outcome telemetry |
| Root revenue line | "$35.0M top line YTD · $7.0M bottom" + "on plan 83%" | Division objective: incremental revenue | 🔴 Hardcoded | `index.html:312` | `topLineYTD=35_000_000`; `bottomYTD = topLine × bottomPct(0.20)` = $7.0M | Dummy | `VALUES["Basco Installed Sales Division"].topLineYTD/.bottomPct` | finance system |
| Verdict chip | "behind 78%" / "on plan 92%" | % of annual plan achieved | 🟡 Sim | `verdict()` `index.html:421`, `attainOf()` `index.html:290` | `target = max(live+1, round(live/attain))`, attain seeded 0.72–0.94; pct = live/target; **≥80% ⇒ "on plan" else "behind"** (threshold hardcoded :421) | Sim (GOA's % is real live ÷ sim target) | `VALUES[name].target` | planning system (plan/target) |
| Ledger "A 2 FTE 17.9 · P 0 FTE 0 · Σ 17.9 FTE" | GOA card | Agents & people counts and FTE, rolled up | 🟡 Sim | `ownAtom()` `index.html:265` | own: agents = 1–2; agentFTE = agents × (8.8–9.7) — **assumption: one agent ≈ 9 person-FTE ("forcemultiplier")**; people: leaf 16%→1, 3%→2, else 0; humanFTE ≈ people × 0.9–1.1. Parent = own + Σ children (`addAtom`) | Sim | `VALUES[name].agents/.agentFTE/.people/.humanFTE` | People & Agent Supply roster; the popover literally says "seeded for now. The named roster is 9.1 content." |
| "EFFORT, HOW HARD" eyebrow | — | Section label | 🔴 Hardcoded | `actBand()` `index.html:439` | — | Label | — | — |
| Analyst-hours hero | "8 analyst hours of work today, **simulated**, at 45s per record" | Human-equivalent effort | 🟡 Sim | `index.html:442` | `round(opsRate × 0.3)` — **assumption**: the 0.3 factor stands in for "45 s per record" translation | Sim (labeled) | formula at :442 | Data Contract v1.1 effort telemetry |
| ops/sec | 26 | Operation rate | 🟡 Sim | `baseRate()` `index.html:285`, jitter in `tickAct()` | leaf: 18–42 seeded; parents: Σ children + own 5–15 (:287); displayed value re-jitters ±10% each second | Sim | `VALUES[name].opsRate` | effort telemetry |
| "N today" counter | 1,081,172 today | Cumulative ops today | 🟡 Sim | `initAct()` ≈`index.html:490` | starts at `opsRate × 43200` (12 h of seconds), accumulates `rate × dt` live | Sim | derived | effort telemetry |
| Sparkline | small blue line | Last 26 ops/sec samples | 🟡 Sim | `paintSpark()` | rolling window of the jittered ops/sec | Sim | — | effort telemetry |
| Activity feed lines | "connector · Request budget: 0/10 used today (UTC)…" (GOA) / "cleared a check #48,540" (others) | What the node is doing | ✅ GOA / 🟡 others | GOA: `/api/activity?limit=12` via `refreshLive()`; others: `FEED` array `index.html:487`, record # starts 48,000 (`recNo` :489) | others: random pick of 10 canned verbs + incrementing # | GOA real, rest sim | `FEED` | activity API (done for GOA) |
| Machinery chips | GOA live: "Root Orchestrator · Source Connector · Normalizer · +5 agents"; others: "Planner ×1 · Executor ×1 · Verifier ×N" | Agent roster | ✅ GOA / 🟡 others | `machinery()` `index.html:449`; GOA roster from `/api/agents` | sim split: plan=30%, exec=50%, verifier=rest of `agents` | GOA real, rest sim | — | agents API (done for GOA) |
| Model rates | "reasoning model 600/min · rules engine 1,300/min · retrieval 275/min" | Engine call rates | 🟡 Sim | `machinery()` | `opsRate × 24 / × 52 / × 11` — **multipliers are invented** | Sim | :449 block | engine telemetry |
| Running cost | "$0.34 / hr · today $4.08" | Cost of the machinery | 🟡 Sim | `baseCost()` `index.html:286` | leaf $0.20–1.20/hr seeded + rollup (+own 0.10–0.50); "today" starts `costHr × 12`, accumulates live | Sim | `VALUES[name].costHr` | billing/usage telemetry |
| "Open human workspace" lip | opens the review queue for GOA | Door to the human's work | ✅ GOA config | label :483; URL `VALUES` `index.html:228` — `workspace: …/?embed=queue` | — | URL real | `VALUES[name].workspace` | 9.2 review-screen registry |
| Expanded band doors | "people … / agents … / spec and engine — reserved" | Sub-doors of expanded node | 🟡 Sim + 🔴 "reserved" | `drawCrumb()` expanded branch | people/agents from ledger above | Sim | — | 9.1 content |

### A3. `window.VALUES` — the real-value entry point (`index.html:228-244`)

| Key | Value | Real? | Purpose |
|---|---|---|---|
| `label` | "Gross Opportunity Agent" | ✅ | presentation name (9.1 note; tree keeps canonical name) |
| `console` | `https://goa-console-1059272334202.us-central1.run.app/` | ✅ | full console, opened by re-clicking the selected card (full-screen modal) |
| `workspace` | same + `?embed=queue` | ✅ | human review workspace ONLY (bottom bar), console top-nav hidden |
| `liveApi` | console base URL | ✅ | polled every **30 s** (`boot()`): `/api/counts?user_id=todd` → `live`; `/api/agents` → roster; `/api/activity?limit=12` → feed. `user_id=todd` is 🔴 hardcoded in `refreshLive()` |

Any node can also pin: `live, target, agents, agentFTE, people, humanFTE, opsRate, costHr, status`, and the root `topLineYTD/bottomPct` — all optional, all roll up through unchanged logic.

### A4. Edges (the connectors are data too)

| Element | Meaning | Type | Origin | Calculation |
|---|---|---|---|---|
| Moving dots on edges | Volume in motion | 🟡 Sim | `dotCount()` | `clamp(round(log10(vol)×3), 1, 14)` dots |
| Flow labels between pipeline siblings (e.g. "917", "1,249") | Records handed to the next stage | 🟡 Sim | `factorOf()` `index.html:289`, pipeline pass `index.html:296-303` | `edgeOut = edgeIn × factor(0.55–1.70 seeded)`; first stage seeds with its own vol (GOA seeds Opportunity Pipeline with the **real 7** when live) |
| ▲ / ▼ triangles | amplifier (factor ≥ 1) / filter (< 1) | 🟡 Sim | same | — |
| Green upward "feed" edge | last pipeline stage feeds the parent | 🟡 Sim | `buildEdges()` | — |

---

## B. Gross Opportunity console — master value table

Everything below binds to the events API (FastAPI `goa/events/http_app.py` → `goa/events/api.py` → `goa/stores/cloudsql.py` → Cloud SQL `tier3/isrds_db`). **All numbers are real production data.**

### B1. Header

| UI Element | Value | Type | Origin | Notes |
|---|---|---|---|---|
| Mark "GOA" + h1 "Gross Opportunity Agent" | — | 🔴 Hardcoded | `screen/index.html:158-161` | correct label per 9.1 note |
| Crumb "Basco · Installed Sales Division · **Danielle's pipeline** · Data Contract v1.0" | — | 🔴 Hardcoded | `screen/index.html:162` | "Danielle" is the PMO-agent persona name — an **assumption/branding choice**, not a real person; contract version is a real doc reference |
| Tabs: Review Queue / Agent Swarm / Live Activity / Pipeline Stats | — | 🔴 Labels | `:166-169` | — |
| Counts strip "3 New · 4 Seen · 7 Active · 0 Rej · 0 Closed" | queue tallies for this reviewer | ✅ Real | `loadCounts()` `:369-374` → `GET /api/counts?user_id=` → `cloudsql.get_counts` (`cloudsql.py:129`) | per-user new/seen via `seen_event`; totals via `opportunity.status` |
| `keys` toggle | shows raw contract keys | 🔴 UI switch | `:173` | dev aid |
| Reviewer identity | `todd` | 🔴 Default | `USER_ID` `:225` — `?user=` param, default `'todd'` | replace with real auth (IAM / login) in production |

### B2. Review Queue (the human review workspace — 9.2)

| UI Element | Type | Origin (API field → DB) | Notes |
|---|---|---|---|
| Row: project name | ✅ | `project_name` → `opportunity.project_name` (from SAM.gov title) | "Unnamed" fallback 🔴 `:388` |
| Badge new/seen | ✅ | `seen_state` (derived per user from `seen_event`) | |
| Badge kept/flagged | ✅ | `gate_passed` → `opportunity.gate_passed` | recall-first coarse gate verdict |
| Score chip e.g. `0.82` | ✅ | `gate_score` → `opportunity.gate_score` | colors 🔴 thresholds `:238`: ≥0.6 green, ≥0.4 amber, else red |
| City/State · record_type · `CSI 10/08` · `bid 2026-07-…` · "2 src" | ✅ | `city,state,record_type,csi_divisions,bid_date,source_links[]` | all normalized from the source payload |
| Detail pane: owner, stage, valuation ($ formatted), location, source link | ✅ | `GET /api/opportunities/{id}` | `valuation` formatted `'$'+toLocaleString()` `:443` |
| Agent decision trail (4 steps: Connector → Normalizer → Dedup → Coarse gate, with matched rules, "consulted model (…)", 🧠 reason) | ✅ | `agent_trace` JSON column, written by the pipeline; engine name = resolved Config Registry role | step titles 🔴 `:416-423` |
| `fetch_state` chip + "Pull full report from SAM.gov" | ✅ | `fetch_state`, `full_record`; POST `/api/opportunities/{id}/full_report` (uses the 2-request UI reserve) | polls every 1.5 s while `pulling` `:471` |
| Reviewer actions Reject (reason + scope radio) / Reopen | ✅ writes | POST `/api/reject`, `/api/reopen`, `/api/seen` | rejections can create screening rules (scope radio) |
| Empty state "No active opportunities… `python -m jobs.backfill --source sam_gov`" | ⚪ | `:383` | shown only when queue empty |

### B3. Agent Swarm tab

| UI Element | Type | Origin | Notes |
|---|---|---|---|
| API request budget bar "sam_gov … 3/10 used" | ✅ | `GET /api/budget` → `api_request_ledger` (per UTC day) + `config/sources/sam_gov.json` `rate_limit` = `{requests_per_second:1, requests_per_day:10, reserve_for_ui:2}` | bar colors 🔴: warn ≥70%, full ≥100% `:304-305`. Quota goes to 1000 via env `GOA_REQUESTS_PER_DAY` after SAM.gov system-account approval |
| 8 agent cards: names/icons/descriptions (Root Orchestrator ◉, Source Connector ⇣, Normalizer ⬒, Dedup Agent ⧉, Screening Gate ⛨, Committer ✓, Watchdog ⏱, Scout ☌) | 🔴 registry (real identities) | `goa/agents_meta.py:15-79` | static **by design** — the swarm roster is code-defined, not fake people |
| status dot "working now / idle / no runs yet" | ✅ derived | `api.py` get_agents: working = logged within **120 s** (threshold 🔴 `api.py:83`) | Labels `STATUS_LABEL` 🔴 `:288` |
| engine chip e.g. "gate_classifier → claude-fable-5 · low" | ✅ config | Config Registry `config/engines.json` via `resolve(role)` | the ONLY place engine bindings live (vendor-neutral rule) |
| counters today / all time / good / drop+warn | ✅ | `agent_activity` aggregates (`cloudsql.get_agent_summaries` `cloudsql.py:198`) | polls every 2.5 s `:364` |
| per-agent log | ✅ | `/api/activity?agent=` | |

### B4. Live Activity tab

| UI Element | Type | Origin |
|---|---|---|
| "● live / ● disconnected" status | ✅ | poll success/failure, 1.5 s interval `:281` |
| Event rows: time · agent · step · message · #run_id | ✅ | `GET /api/activity` → `agent_activity` table (id, ts, run_id, step, level, message, agent) |
| Empty state "Run `scripts/run_backfill.sh`…" | ⚪ | `:248` |

### B5. Pipeline Stats tab (the charts)

All from `GET /api/stats` (`cloudsql.get_stats` `cloudsql.py:286`) — **real SQL aggregates**, no mock data. Rendered as pure-CSS horizontal bars (no chart library): X = count `n` (width `= n/max×100%`), Y = category label.

| Chart | Represents | Dataset |
|---|---|---|
| Stat tiles: Total pulled / Gate kept / Gate dropped / Dups collapsed / Active / Rejected / Full reports | Lifetime pipeline totals | `totals.*`, `dedup.collapsed` |
| Pipeline funnel: Source records → After dedup → Kept by gate → In review | Volume at each pipeline stage | `dedup.source_records`, `dedup.opportunities`, `totals.gate_kept`, `totals.active`. ⚠ Quirk: `:512` has a no-op ternary (`t.active-t.rejected>=0?t.active:t.active`) — both branches render `t.active` |
| By record type | Mix of active_bid/itb/permit_signal/… | `by_record_type[]` |
| Top states | Geographic spread | `by_state[]` |
| Gate-score distribution (5 buckets 0.0–1.0) | Confidence histogram of active records | `score_buckets[]`, bucket labels 🔴 `:521` |
| By source | Which registered source produced records | `by_source[]` (today: `sam_gov` only) |
| Funnel caption "Every stage is real: SAM.gov pull → normalize → dedup … recall-first" | 🔴 label `:514` — and accurate | — |

**Org-chart sparkline** (Surface A) is the only other "chart": 26-point rolling ops/sec window, 🟡 simulated (see A2).

---

## 3. Organization chart nodes

- **133 nodes, zero people** — every node is a *capability/function*, not an employee. There are **no employee names anywhere** on either surface ("Danielle" in the console crumb is an agent persona; "todd" is a demo reviewer id).
- Hierarchy: 1 Subsidiary → 7 Divisions → 78 Departments → 8 Sub-Departments (under Demand Generation ▸ Projects) → 39 Functions. Levels are assigned **purely by indent depth** in `naming_tree.txt` (`generate_data.py:22-24`).
- Source of truth: `agents/bisd/org-chart/naming_tree.txt` = the BISD Business System Naming Tree (business-authored, real org design). Parent/child = the indentation itself; the "Projects" subtree exists because the value-chain project functions were re-homed under Demand Generation in the updated tree.
- Production fetch: replace the generated `window.DATA` with an API returning the same `{name, children[], level}` shape; everything else keeps working (the render layer only reads that shape).

---

## 4. Data flow

**Surface A (org chart):**
```
naming_tree.txt ──generate_data.py──▶ window.DATA (index.html:204)
                                          │ d3.hierarchy
window.VALUES (index.html:228) ──▶ MODEL.build()  ◀── seeded PRNG (sim slots)
   ▲ real numbers, hand-typed              │ derive()
   │                                       ▼
   └── refreshLive() 30s poll ──▶ paintFace()/render() ──▶ DOM (nodes, edges, band)
        GET /api/counts │ /api/agents │ /api/activity   (goa-console, CORS *)
```

**Surface B (console):**
```
SAM.gov ──jobs/delta (Cloud Run job, 00:30 UTC scheduler)──▶ goa/orchestrator.py
  ▶ adapters/rest_adapter ▶ normalize ▶ dedup ▶ gate (engines.json roles) ▶ stores/cloudsql
                                                                  │
Cloud SQL tier3/isrds_db  ◀──────────────────────────────────────┘
      ▲ asyncpg pool (stores/cloudsql.py)
goa/events/api.py ◀── goa/events/http_app.py (FastAPI, CORS) ◀── screen/index.html fetch()
      ▲ agents_meta.py (roster) · config_registry (engine chips) · api_request_ledger (budget)
```

---

## 5. Files responsible

| File | Responsibility |
|---|---|
| `agents/bisd/org-chart/index.html` | Entire Surface A: DATA (tree), VALUES (real overrides), MODEL (simulated math), render/interaction, live GOA wiring, workspace modal |
| `agents/bisd/org-chart/naming_tree.txt` | Source of truth for the org tree |
| `agents/bisd/org-chart/generate_data.py` | Splices the tree into `window.DATA` |
| `agents/gross-opportunity-agent/screen/index.html` | Entire Surface B UI (queue, swarm, activity, stats, embed mode) |
| `goa/events/http_app.py` | FastAPI routes `/api/*`, CORS, serves the screen |
| `goa/events/api.py` | API layer; derives agent status; engine-binding display |
| `goa/stores/cloudsql.py` | Every SQL read/write (counts :129, summaries :198, stats :286, watermark :530) |
| `goa/agents_meta.py` | The 8 swarm-agent identities (names, icons, "does") |
| `config/engines.json` | Config Registry — the only place model bindings live |
| `config/sources/sam_gov.json` | Source config incl. `rate_limit` (10/day, 2 UI reserve) |
| `sql/cloudsql_ddl.sql` | Serving-store schema (12 tables) |
| `jobs/seed.py`, `jobs/delta.py` | Scope/rules/source seeding; nightly pull |

---

## 6. Missing backend integration (what still needs real data)

| Simulated value (Surface A) | Expected API | Expected fields | Suggested model |
|---|---|---|---|
| Status/last-active for all non-GOA nodes; activate event | Data Contract **v1.1 addendum** (announced by the 9.1 note, not yet frozen) | `status`, `last_active`, `activate` | per-capability heartbeat table |
| ops/sec, analyst-hours, "today" counters, sparkline | v1.1 effort telemetry | `ops_rate`, `ops_today`, `analyst_hours_today` | time-series (BigQuery or Cloud Monitoring) |
| A/P FTE ledger + machinery roster per node | People & Agent Supply systems ("9.1 content") | `agents[]`, `people[]`, FTE | roster service |
| live/target ("N / yr", on-plan %) per node | planning system | `actual`, `plan` | plan-of-record table |
| Root revenue $35.0M/$7.0M/20% | finance system | `top_line_ytd`, `bottom_pct` | finance rollup |
| Model rates & running cost | engine/billing telemetry | per-role call rates, $ | usage ledger |
| Reviewer identity `todd` (both surfaces) | real auth | user principal | IAM header / login |

Until each lands, pin real numbers by hand in `window.VALUES` — that block exists precisely as the bridge.

---

## 7. Final summary

**✅ Real** — GOA card outcome 7/yr & "3 new" (counts API); GOA feed lines & machinery roster (activity/agents APIs); LIVE badge; all 133 node names & hierarchy; every number on the console (counts, queue fields, trace, budget 3/10, agent counters, stats/funnel/charts); engine chips (engines.json); workspace/console URLs.

**🟡 Simulated (deterministic, labeled as such on screen)** — for every non-GOA node: status lights, N/yr outcomes, on-plan/behind %, FTE ledgers, ops/sec, analyst-hours, today-counters, sparkline, feed lines, machinery chips, model rates, running cost, edge volumes/factors. Replace via `window.VALUES` or Contract v1.1. *(Formulas: §A2.)*

**🔴 Hardcoded** — root revenue $35.0M/20%; thresholds (on-plan 80%, score colors 0.6/0.4, budget bar 70/100%, working-status 120 s); `user_id=todd` defaults; the 9× agent-FTE assumption; model-rate multipliers ×24/×52/×11; FEED verbs + recNo 48000; all labels/captions ("Danielle's pipeline", funnel caption, hints); ALIASES; colors; SZ/ACCENT; poll intervals 30 s/2.5 s/1.5 s.

**⚪ Placeholder** — empty states ("No active opportunities…", "No activity yet…", "Loading…"), "spec and engine — reserved" door, "unresolved → model resolver" toast.

**Known quirks (assumptions the AI made, worth a human decision):** the `0.3` analyst-hours factor vs the stated "45s per record"; the no-op ternary in the stats funnel (`screen/index.html:512`); `new URLSearchParams` embed-guard runs before `<body>` so it stamps `<html>` not `<body>`; "Danielle's pipeline" branding; GOA's *target* (hence its behind-%) is still simulated even when *live* is real.

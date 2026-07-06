# BISD Operations Surface (org chart)

The interactive Basco Installed Sales Division operations surface — the ATOM
dark-instrument org chart. One self-contained `index.html` (D3 bundled inline):

```
Basco Installed Sales Division           (Subsidiary — incremental revenue)
 └─ 7 Divisions                          Leadership · Demand Generation ·
     └─ 78 Departments                   People and Agent Supply · Product and
         └─ 8 Sub-Departments (Projects) Service Supply · Order Management ·
             └─ 39 Functions             Infrastructure · Technology and Systems
```

Every node is a four-state capability widget per the **Agent Operations Widget
spec v1.0**: minimized → normal → selected → expanded, each a superset of the
last, over a crown band (name · status), the outcome line with an on-plan /
behind verdict, the A/P/Σ FTE ledger, the effort band (analyst hours, ops/sec,
live activity feed), and machinery (roster, model rates, running cost). Views
blend and tween; navigation is nodes + breadcrumb + the bottom command bar
(type "take me to opportunity pipeline"). No global menu bar.

## Files

| File | What it is |
|---|---|
| `index.html` | The whole surface. Deployed as-is to GCS. |
| `naming_tree.txt` | Source of truth for the tree — the BISD Business System Naming Tree, 4-space indents. |
| `generate_data.py` | Rebuilds `window.DATA` inside `index.html` from `naming_tree.txt`. |

The previous spec-driven chart (`generate_tree.py`, `tree.data.js`) is
superseded; see git history if needed.

## Easy value definitions

All metrics are **simulated, deterministic by node path** (placeholders pending
9.1 + telemetry). To pin real numbers, edit the `window.VALUES` block near the
top of `index.html` — keyed by exact node name, every field optional, real
values drop into the same slots and roll up the tree:

```js
window.VALUES={
  "Gross Opportunity Agent Capability":{
    label:"Gross Opportunity Agent",          // presentation name
    workspace:"…/?embed=queue",               // bottom bar → human review workspace ONLY
    liveApi:"https://goa-console-…run.app"    // real counts/roster/feed, polled every 30s
    // live, target, agents, agentFTE, people, humanFTE, opsRate, costHr, status
  },
  // "Basco Installed Sales Division": { topLineYTD: 42000000, bottomPct: 0.25 },
};
```

## Gross Opportunity Agent — live wiring (9.1 note applied)

- The **analytics live inside the widget**: when the deployed GOA console is
  reachable, `/api/counts` drops `total_active` into the outcome slot,
  `/api/agents` fills the machinery roster, and `/api/activity` feeds the
  expanded activity band. The badge gains a `LIVE` legend line. Offline, the
  simulated values stay put — the chart never breaks.
- **Status state machine**: the agent rests in Standby; a reachable console
  wakes it to Working, and on a selected/expanded card the status is
  **tap-to-activate** for demonstrations.
- The bottom bar (**Open human workspace**) opens ONLY the human review
  workspace — the de-duplicated leads queue (9.2 review screen,
  `?embed=queue` hides the console's global nav) — as a **slide-up panel**
  over the surface with a "Back to the surface" way home.
- Engine, Data Contract v1.0 binding, and the backend are untouched.

## Regenerate after the naming tree changes

```bash
python3 generate_data.py     # reads naming_tree.txt, splices window.DATA
```

## View locally

Double-click `index.html` (fully self-contained), or:

```bash
python -m http.server        # http://localhost:8000/agents/bisd/org-chart/
```

## Deploy

```bash
gcloud storage cp index.html gs://isrds-bisd-org-chart/index.html
# live at https://storage.googleapis.com/isrds-bisd-org-chart/index.html
```

# BISD Org Chart

Interactive org chart of the Basco Installed Sales Division agentic function tree:

```
Basco
 └─ 4 value-chain stages
     └─ 9 sub-departments
         └─ 34 agentic functions   (each a small "dashboard" cell)
```

Click any node to expand its children; click again to collapse. Leaf cells show
each function's business object, posture, human gates, skill-mesh size, and runtime
state (dormant / blocked / prototype). **Publicized Project Aggregation** (the Gross
Opportunity queue) is the one function with a working review-screen prototype and
links out to it.

## Files

| File | What it is |
|---|---|
| `index.html` | The viewer. Self-contained; reads `tree.data.js`. |
| `generate_tree.py` | Reads every `agent_spec.yaml` and regenerates the data. Source of truth. |
| `tree.data.js` | Generated & committed — `window.BISD_TREE = {...}`, consumed by `index.html`. The chart works on a fresh clone with no build step. |
| `tree.json` | Generated but **git-ignored** (repo ignores `*.json`). Same tree as plain JSON, for wiring into a real app or tests. Run the generator to produce it locally. |

## Regenerate after specs change

```bash
cd agents/bisd/org-chart
pip install pyyaml          # one-time
python generate_tree.py
```

The chart shape is driven entirely by the specs, **except** two curation layers
defined at the top of `generate_tree.py`:

- `STAGES` — how the 9 sub-departments group into the 4 value-chain stages
  (the specs only carry `sub_department`, not the higher stage grouping).
- `PROTO` — which function has a live prototype (link + placeholder metrics).

Edit those two dicts if the grouping or prototype status changes.

## View it

Double-click `index.html`. If your browser blocks the local `tree.data.js` include
over `file://`, serve the folder instead:

```bash
python -m http.server
# then open http://localhost:8000/agents/bisd/org-chart/
```

## Notes for the developer

- All 34 functions are **dormant** (`is_active: false`) until each clears the
  8-phase build pipeline. The chart reflects that state truthfully.
- The Gross Opportunity prototype cell is **live**: its "Open →" opens the
  deployed console (https://goa-console-1059272334202.us-central1.run.app/) and
  the active/new numbers are fetched from `/api/counts` every 30s (CORS-enabled
  GET). The header badge flips SIMULATED → LIVE on first successful fetch; if
  the API is unreachable the `sim` placeholder numbers stay, so the chart never
  breaks offline. Configured in `generate_tree.py` → `PROTO[...].live_api`.
- Design tokens (colors, Inter + IBM Plex Mono) match the Gross Opportunity
  review-screen prototype so the two surfaces read as one system.
- This is a static visualization. To make it live, replace the generated
  `tree.data.js` with an API call that returns the same tree shape, and swap the
  simulated metrics for real per-function telemetry.

"""
PMO Swarm Control UI — a small FastAPI web app (Cloud Run Service).

Gives a non-developer a browser dashboard to:
  • see the swarm (orchestrator + 5 sub-agents) and what each does
  • read the latest Operating Briefs
  • read the Trust Ledger (every governed decision)
  • review PENDING APPROVALS and Approve/Decline them — Approve posts the
    drafted comment to Jira (the human side of the governance gate)
  • trigger a PMO cycle on demand (runs the existing Cloud Run Job)

It reuses the agent's own modules (db, jira_client, governance) so there is a
single source of truth. Connects to the same Cloud SQL Postgres instance.

Run locally:   uvicorn ui.server:app --reload --port 8080
Deploy:        ./ui/deploy_ui.sh
"""
import os
import json
import logging

import httpx
from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from adk.shared.governance import trust_ledger_read, trust_ledger_log
from adk.shared import jira_client as jc

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("pmo-ui")

PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "isr-division-systems-488723")
REGION  = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
JOB     = os.environ.get("PMO_JOB_NAME", "pmo-swarm")

app = FastAPI(title="PMO Swarm Control")

AGENTS = [
    ("🧭 pmo_orchestrator", "Orchestrator", "Routes work to sub-agents, enforces governance gates, writes the Operating Brief."),
    ("1️⃣ execution_tracking_agent", "DECIDE & REPORT", "Scans every board for stalled / at-risk tickets."),
    ("2️⃣ follow_up_agent", "MUST ESCALATE", "Drafts chase & escalation messages (gated for human approval)."),
    ("3️⃣ ownership_raci_agent", "MUST ESCALATE", "Audits RACI ownership gaps."),
    ("4️⃣ feature_completeness_agent", "DECIDE & REPORT", "Tracks feature build completion."),
    ("5️⃣ hygiene_agent", "DECIDE & REPORT", "Detects field/status hygiene violations."),
]


# ── Data access ───────────────────────────────────────────────────────────────

async def _db_pool():
    from adk.shared.db import get_pool
    return await get_pool()


async def get_briefs(limit: int = 10):
    pool = await _db_pool()
    if pool is None:
        return []
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT briefing_date, summary_text, generated_by_role "
            "FROM daily_briefings WHERE swarm_id='pmo-swarm' "
            "ORDER BY briefing_date DESC LIMIT $1", limit)
    return [dict(r) for r in rows]


async def get_agent_activity(limit: int = 400):
    """Group recent Trust Ledger entries by agent (role_category) so the UI can
    show what the orchestrator and each sub-agent have actually been doing."""
    pool = await _db_pool()
    rows = []
    if pool is None:
        for e in trust_ledger_read(limit):
            rows.append({"agent": e.get("agent_id", "?"), "event": e.get("type", ""),
                         "detail": e.get("detail", ""), "ts": e.get("timestamp", "")})
    else:
        async with pool.acquire() as conn:
            recs = await conn.fetch(
                "SELECT role_category, event_type, evidence, created_at "
                "FROM trust_ledger WHERE swarm_id='pmo-swarm' "
                "ORDER BY created_at DESC LIMIT $1", limit)
        for r in recs:
            rows.append({"agent": r["role_category"], "event": r["event_type"],
                         "detail": _ev_detail(r["evidence"]), "ts": str(r["created_at"])[:19]})
    by_agent = {}
    for r in rows:
        by_agent.setdefault(r["agent"] or "unknown", []).append(r)
    return by_agent


async def get_ledger_db(limit: int = 50):
    pool = await _db_pool()
    if pool is None:
        return [{"detail": e.get("detail", ""), "type": e.get("type"),
                 "created_at": e.get("timestamp", ""), "agent_id": e.get("agent_id", "")}
                for e in trust_ledger_read(limit)]
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT role_category, event_type, decision_class, evidence, created_at "
            "FROM trust_ledger WHERE swarm_id='pmo-swarm' "
            "ORDER BY created_at DESC LIMIT $1", limit)
    return [dict(r) for r in rows]


import re
_TICKET_RE = re.compile(r"[A-Z][A-Z0-9]+-\d+")


def _ticket_of(detail: str) -> str:
    """Extract the Jira key from any gate/escalation detail format:
       'Review: ... [ISRDS-1489]'  OR  'ISRDS-1489 → Mignonne V. | 137h | ...'"""
    if "[" in detail and detail.rstrip().endswith("]"):
        inside = detail.rsplit("[", 1)[1].rstrip("]").strip()
        if _TICKET_RE.fullmatch(inside):
            return inside
    m = _TICKET_RE.search(detail)
    return m.group(0) if m else ""


def _human_message(detail: str, ticket: str) -> str:
    """Turn a stored gate/escalation detail into the human-voiced body to post.
       Strips the 'TICKET → Owner | 137h |' prefix and any 'Review:' / '[TICKET]'
       wrappers so only the real message remains."""
    text = detail
    # drop a trailing [TICKET]
    if "[" in text and text.rstrip().endswith("]"):
        text = text.rsplit("[", 1)[0].strip()
    # 'Review: <msg>' → '<msg>'
    if text.lower().startswith(("review:", "approve:", "escalate:")):
        text = text.split(":", 1)[1].strip()
    # 'ISRDS-1489 → Owner | 137h | <msg>' → '<msg>'  (take part after last '|')
    if "|" in text:
        text = text.split("|")[-1].strip()
    return text or detail


def _ev_detail(evidence) -> str:
    if isinstance(evidence, str):
        try: evidence = json.loads(evidence)
        except Exception: return evidence
    if isinstance(evidence, dict):
        return evidence.get("detail", "")
    return ""


async def pending_actions_db(limit: int = 200):
    """Read fully-formed pending Jira comments (real human message + @mention target).
    These are the preferred source: the message is post-ready, not meta-text."""
    pool = await _db_pool()
    if pool is None:
        return []
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id, ticket_key, assignee_name, assignee_id, message, urgency, created_at "
                "FROM pending_actions WHERE swarm_id='pmo-swarm' AND status='pending' "
                "ORDER BY created_at DESC LIMIT $1", limit)
    except Exception:
        return []   # table may not exist yet (no cycle has run since deploy)
    return [{"id": str(r["id"]), "ticket": r["ticket_key"],
             "assignee_name": r["assignee_name"] or "", "assignee_id": r["assignee_id"] or "",
             "message": r["message"], "urgency": r["urgency"] or "",
             "ts": str(r["created_at"])[:19], "source": "action"} for r in rows]


async def pending_gates(last_n: int = 300):
    """Opened gates (ESCALATION_PENDING) with no later ESCALATION_RESOLVED for the
    same ticket. Read from the shared Cloud SQL trust_ledger so the UI sees what
    the Job wrote. Falls back to the local JSONL when DB is unavailable."""
    pool = await _db_pool()
    if pool is None:
        entries = trust_ledger_read(last_n)
        resolved = {e.get("detail", "").split("|", 1)[0].strip()
                    for e in entries if e.get("type") in ("approval", "rejection")}
        seen, out = set(), []
        for e in entries:
            if e.get("type") != "gate":
                continue
            d = e.get("detail", "")
            if d in seen or _ticket_of(d) in resolved:
                continue
            seen.add(d)
            out.append({"detail": d, "ticket": _ticket_of(d), "ts": e.get("timestamp", "")[:19]})
        return out

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT event_type, evidence, created_at FROM trust_ledger "
            "WHERE swarm_id='pmo-swarm' AND event_type IN "
            "('ESCALATION_PENDING','ESCALATION_RESOLVED') "
            "ORDER BY created_at ASC LIMIT $1", last_n)
    resolved, opened, seen = set(), [], set()
    for r in rows:
        detail = _ev_detail(r["evidence"])
        tk = _ticket_of(detail)
        if r["event_type"] == "ESCALATION_RESOLVED":
            resolved.add(tk)
        else:
            opened.append((detail, tk, r["created_at"]))
    out = []
    for detail, tk, ts in reversed(opened):
        if detail in seen or (tk and tk in resolved):
            continue
        seen.add(detail)
        out.append({"detail": detail, "ticket": tk, "ts": str(ts)[:19]})
    return out


# ── HTML rendering ──────────────────────────────────────────────────────────

def _page(body: str, stats: dict | None = None) -> str:
    stats = stats or {}
    chips = ""
    for label, val in [("Pending", stats.get("pending", 0)),
                       ("Agents", stats.get("agents", 6)),
                       ("Briefs", stats.get("briefs", 0)),
                       ("Decisions", stats.get("decisions", 0))]:
        chips += (f'<div class="stat"><div class="stat-v">{val}</div>'
                  f'<div class="stat-l">{label}</div></div>')
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>PMO Swarm Control · ISRDS</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{{
  --bg:#f6f7f9; --surface:#ffffff; --surface-2:#f9fafb; --border:#e5e7eb;
  --text:#0f172a; --text-2:#475569; --text-3:#94a3b8;
  --accent:#e4002b; --accent-soft:rgba(228,0,43,.07); --accent-text:#c20025;
  --ok:#16a34a; --ok-soft:rgba(22,163,74,.08); --warn:#b45309;
  --radius:16px; --radius-nested:12px; --radius-pill:999px;
  --shadow:0 1px 2px rgba(16,24,40,.04),0 4px 16px rgba(16,24,40,.06);
}}
*{{box-sizing:border-box}}
html,body{{margin:0;padding:0;overflow-x:hidden}}
body{{font-family:Inter,-apple-system,Segoe UI,Roboto,sans-serif;font-size:16px;line-height:1.55;
  background:var(--bg);color:var(--text);-webkit-font-smoothing:antialiased}}
a{{color:var(--text);text-decoration:none}}
.topbar{{position:sticky;top:0;z-index:10;background:rgba(255,255,255,.85);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border);padding:16px 24px;display:flex;align-items:center;gap:16px}}
.brand{{display:flex;align-items:center;gap:12px}}
.brand .logo{{width:36px;height:36px;border-radius:10px;display:grid;place-items:center;font-size:18px;
  background:var(--accent-soft);border:1px solid rgba(228,0,43,.18)}}
.brand h1{{font-size:18px;font-weight:700;margin:0;letter-spacing:-.01em}}
.brand .sub{{font-size:12px;color:var(--text-3);font-weight:500}}
.pill{{font-size:12px;font-weight:600;padding:6px 12px;border-radius:var(--radius-pill);
  background:var(--ok-soft);color:var(--ok);border:1px solid rgba(22,163,74,.2)}}
.refresh{{margin-left:auto;font-size:14px;font-weight:600;color:var(--text-2);
  padding:8px 16px;border-radius:var(--radius-nested);border:1px solid var(--border);background:var(--surface)}}
.refresh:hover{{color:var(--text);border-color:var(--text-3)}}
main{{max-width:1080px;margin:0 auto;padding:32px 24px 64px}}
.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:32px}}
.stat{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:20px 24px;box-shadow:var(--shadow)}}
.stat-v{{font-size:32px;font-weight:700;line-height:1;letter-spacing:-.02em}}
.stat-l{{font-size:13px;color:var(--text-3);font-weight:500;margin-top:8px;
  text-transform:uppercase;letter-spacing:.06em}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);
  padding:24px;margin-bottom:24px;box-shadow:var(--shadow)}}
.card h2{{font-size:13px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;
  color:var(--text-3);margin:0 0 20px;display:flex;align-items:center;gap:10px}}
.card h2 .count{{color:var(--text-2)}}
.pending{{border:1px solid var(--border);border-left:3px solid var(--accent);
  background:var(--surface-2);padding:16px 20px;border-radius:var(--radius-nested);margin-bottom:16px}}
.pending .head{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-bottom:10px}}
.tk{{font-weight:700;color:var(--accent-text);font-size:15px}}
.badge{{font-size:11px;font-weight:600;padding:3px 10px;border-radius:var(--radius-pill);
  background:var(--surface);border:1px solid var(--border);color:var(--text-2);text-transform:capitalize}}
.msg{{color:var(--text);font-size:15px;margin:8px 0 16px;line-height:1.6}}
.msg .lbl{{display:block;font-size:11px;font-weight:600;text-transform:uppercase;
  letter-spacing:.06em;color:var(--text-3);margin-bottom:6px}}
.actions{{display:flex;gap:10px;flex-wrap:wrap}}
.btn{{font-family:inherit;border:1px solid var(--border);border-radius:var(--radius-nested);
  padding:10px 20px;font-size:14px;font-weight:600;cursor:pointer;background:var(--surface);color:var(--text)}}
.btn:hover{{border-color:var(--text-3)}}
.btn.primary{{background:var(--ok);border-color:var(--ok);color:#fff}}
.btn.primary:hover{{filter:brightness(1.08)}}
.btn.accent{{background:var(--accent);border-color:var(--accent);color:#fff}}
.btn.accent:hover{{filter:brightness(1.08)}}
.btn.sm{{padding:6px 12px;font-size:12px}}
.btn:focus-visible{{outline:2px solid var(--accent);outline-offset:2px}}
details{{border:1px solid var(--border);border-radius:var(--radius-nested);
  background:var(--surface-2);margin-bottom:12px;overflow:hidden}}
details[open]{{border-color:var(--text-3)}}
summary{{cursor:pointer;padding:16px 20px;list-style:none;display:flex;align-items:center;gap:12px;flex-wrap:wrap}}
summary::-webkit-details-marker{{display:none}}
summary .ico{{font-size:22px}}summary b{{font-size:16px;font-weight:600}}
summary .desc{{flex-basis:100%;font-size:13px;color:var(--text-3);font-weight:400;margin-top:2px}}
details .body{{padding:0 20px 16px}}
.row{{display:flex;justify-content:space-between;gap:16px;padding:10px 0;
  border-top:1px solid var(--border);font-size:14px}}
.row .t{{color:var(--text-2);min-width:0;overflow-wrap:anywhere}}
.row .ts{{color:var(--text-3);font-size:12px;white-space:nowrap;font-variant-numeric:tabular-nums}}
pre{{white-space:pre-wrap;background:var(--bg);border:1px solid var(--border);padding:16px;
  border-radius:var(--radius-nested);font-size:13px;line-height:1.6;color:var(--text-2);
  max-height:340px;overflow:auto;font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
.muted{{color:var(--text-3);font-size:13px}}
.empty{{color:var(--text-3);font-style:italic;padding:8px 0}}
.run-row{{display:flex;align-items:center;gap:16px;flex-wrap:wrap}}
@media(max-width:680px){{.stats{{grid-template-columns:repeat(2,1fr)}}main{{padding:20px 16px 48px}}}}
</style></head><body>
<div class=topbar>
  <div class=brand>
    <div class=logo>🛰️</div>
    <div><h1>PMO Swarm Control</h1><div class=sub>ISRDS · Danielle, Operating Brief Agent</div></div>
  </div>
  <span class=pill>● Live</span>
  <a class=refresh href="/">Refresh</a>
</div>
<main>
  <div class=stats>{chips}</div>
  {body}
</main></body></html>"""


@app.get("/", response_class=HTMLResponse)
async def home(msg: str = "", kind: str = ""):
    actions = await pending_actions_db()           # preferred: real human messages
    gates = await pending_gates()
    briefs = await get_briefs(5)
    ledger = await get_ledger_db(60)
    activity = await get_agent_activity()

    # Merge: pending_actions first (post-ready human text), then any legacy gates
    # whose ticket isn't already covered by an action.
    action_tickets = {a["ticket"] for a in actions}
    items = []
    for a in actions:
        items.append({"ticket": a["ticket"], "ts": a["ts"], "message": a["message"],
                      "urgency": a["urgency"], "to": a["assignee_name"]})
    for g in gates:
        if g["ticket"] and g["ticket"] in action_tickets:
            continue
        items.append({"ticket": g["ticket"], "ts": g["ts"],
                      "message": _human_message(g["detail"], g["ticket"]),
                      "urgency": "", "to": ""})

    total_pending = len(items)
    if items:
        pend = ""
        for it in items:
            tk = it["ticket"]
            if tk:
                buttons = (
                    f'<div class=actions>'
                    f'<form method=post action="/approve">'
                    f'<input type=hidden name=ticket value="{tk}">'
                    f'<button class="btn primary" type=submit>Approve &amp; post</button></form>'
                    f'<form method=post action="/decline">'
                    f'<input type=hidden name=ticket value="{tk}">'
                    f'<button class="btn" type=submit>Decline</button></form></div>')
                tk_label = f'<span class=tk>{tk}</span>'
            else:
                buttons = '<span class=muted>⚠ No ticket key — cannot auto-post</span>'
                tk_label = '<span class=muted>(no ticket)</span>'
            urg_badge = f'<span class=badge>{it["urgency"]}</span>' if it.get("urgency") else ""
            to_badge = f'<span class=badge>to {it["to"]}</span>' if it.get("to") else ""
            pend += f"""<div class=pending>
              <div class=head>{tk_label}{urg_badge}{to_badge}<span class=muted style="margin-left:auto">{it['ts']}</span></div>
              <div class=msg><span class=lbl>Message to post</span>{it['message']}</div>
              {buttons}
            </div>"""
    else:
        pend = "<p class=empty>No pending approvals. 🎉</p>"

    # Map the agent_id used in the ledger → friendly icon+name+description
    AGENT_META = {
        "pmo_orchestrator":            ("🧭", "Orchestrator", "Routes work, enforces gates, writes the brief."),
        "pmo_execution_tracking":      ("1️⃣", "Execution Tracking", "Scans boards for stalled / at-risk tickets."),
        "execution_tracking_agent":    ("1️⃣", "Execution Tracking", "Scans boards for stalled / at-risk tickets."),
        "pmo_follow_up":               ("2️⃣", "Follow-up & Escalation", "Drafts chase & escalation messages (gated)."),
        "follow_up_agent":             ("2️⃣", "Follow-up & Escalation", "Drafts chase & escalation messages (gated)."),
        "pmo_ownership_raci":          ("3️⃣", "Ownership / RACI", "Resolves who is Accountable & Responsible."),
        "ownership_raci_agent":        ("3️⃣", "Ownership / RACI", "Resolves who is Accountable & Responsible."),
        "pmo_feature_completeness":    ("4️⃣", "Feature Completeness", "Tracks feature build completion."),
        "feature_completeness_agent":  ("4️⃣", "Feature Completeness", "Tracks feature build completion."),
        "pmo_hygiene":                 ("5️⃣", "Hygiene", "Detects field/status hygiene violations."),
        "hygiene_agent":               ("5️⃣", "Hygiene", "Detects field/status hygiene violations."),
        "human":                       ("🧑", "Human (you)", "Approvals & declines via this UI."),
    }

    def _agent_block(agent_id, acts):
        icon, name, desc = AGENT_META.get(agent_id, ("🤖", agent_id, ""))
        recent = "".join(
            f"<div class=row><span class=t>{a['event']} · {a['detail'][:160]}</span>"
            f"<span class=ts>{a['ts']}</span></div>"
            for a in acts[:10])
        return (f"<details><summary><span class=ico>{icon}</span>"
                f"<b>{name}</b><span class=badge>{len(acts)} actions</span>"
                f"<span class=desc>{desc}</span></summary>"
                f"<div class=body>{recent or '<p class=empty>No recorded actions yet.</p>'}</div>"
                f"</details>")

    # Order: orchestrator first, then numbered sub-agents, then human, then anything else
    order = ["pmo_orchestrator", "pmo_execution_tracking", "execution_tracking_agent",
             "pmo_follow_up", "follow_up_agent", "pmo_ownership_raci", "ownership_raci_agent",
             "pmo_feature_completeness", "feature_completeness_agent",
             "pmo_hygiene", "hygiene_agent", "human"]
    seen_agents, agents_html = set(), ""
    for aid in order:
        if aid in activity and aid not in seen_agents:
            agents_html += _agent_block(aid, activity[aid]); seen_agents.add(aid)
    for aid, acts in activity.items():
        if aid not in seen_agents:
            agents_html += _agent_block(aid, acts); seen_agents.add(aid)
    if not agents_html:
        agents_html = "".join(
            f"<div class=agent><div style=font-size:20px>{i.split(' ')[0]}</div>"
            f"<div><b>{' '.join(i.split(' ')[1:])}</b><span class=role>{r}</span>"
            f"<span class=desc>{d}</span></div></div>"
            for i, r, d in AGENTS)

    briefs_html = "".join(
        f"<details><summary><span class=ico>📄</span>"
        f"<b>{b['briefing_date']}</b>"
        f"<span class=badge>{b.get('generated_by_role','')}</span></summary>"
        f"<div class=body><pre>{(b['summary_text'] or '')[:12000]}</pre></div></details>"
        for b in briefs) or "<p class=empty>No briefs yet. Run a cycle below.</p>"

    led_html = "".join(
        f"<div class=row><span class=t>{(e.get('event_type') or e.get('type') or '')} · "
        f"{_evidence_str(e)}</span>"
        f"<span class=ts>{str(e.get('created_at',''))[:19]}</span></div>"
        for e in ledger) or "<p class=empty>No ledger entries.</p>"

    banner = ""
    if msg:
        bg = "var(--ok-soft)" if kind == "ok" else ("rgba(217,119,6,.14)" if kind == "warn" else "var(--surface-2)")
        bc = "var(--ok)" if kind == "ok" else ("var(--warn)" if kind == "warn" else "var(--border)")
        banner = (f'<div style="background:{bg};border:1px solid {bc};color:var(--text);'
                  f'padding:14px 18px;border-radius:var(--radius-nested);margin-bottom:24px;'
                  f'font-weight:600">{msg}</div>')

    body = f"""
    {banner}
    <div class=card>
      <h2>Pending Human Approvals <span class=count>· {total_pending}</span>
        <form method=post action="/clear" style="margin-left:auto"
          onsubmit="return confirm('Clear ALL pending approvals? They will not be posted to Jira.')">
          <button class="btn sm" type=submit>Clear all</button>
        </form>
      </h2>{pend}
    </div>
    <div class=card>
      <h2>Run the swarm</h2>
      <div class=run-row>
        <form method=post action="/run"><button class="btn accent" type=submit>Run PMO cycle</button></form>
        <span class=muted>Triggers <code>{JOB}</code> · ~2 min · refresh after.</span>
      </div>
    </div>
    <div class=card>
      <h2>Live Agent Activity</h2>
      <p class=muted style="margin-top:-12px;margin-bottom:18px">
        One orchestrator + five sub-agents. Expand any agent to see its recent actions.</p>
      {agents_html}
    </div>
    <div class=card><h2>Operating Briefs</h2>{briefs_html}</div>
    <div class=card><h2>Trust Ledger · Decision Stream</h2>{led_html}</div>
    """
    stats = {"pending": total_pending, "agents": len(activity) or 6,
             "briefs": len(briefs), "decisions": len(ledger)}
    return _page(body, stats)


def _evidence_str(e: dict) -> str:
    ev = e.get("evidence")
    if isinstance(ev, str):
        try: ev = json.loads(ev)
        except Exception: return ev[:120]
    if isinstance(ev, dict):
        return (ev.get("detail") or json.dumps(ev))[:120]
    return e.get("detail", "")[:120]


async def _record_resolution(ticket: str, detail: str) -> None:
    """Write an ESCALATION_RESOLVED row so the gate stops showing as pending."""
    pool = await _db_pool()
    if pool is None:
        return
    from adk.shared.config_registry import get_tenant_id
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO trust_ledger (tenant_id, swarm_id, role_category, "
            "event_type, decision_class, evidence) VALUES ($1,$2,$3,$4,$5,$6)",
            get_tenant_id(), "pmo-swarm", "human",
            "ESCALATION_RESOLVED", "HUMAN_GATE",
            json.dumps({"detail": f"{detail} [{ticket}]"}))


def _flash(msg: str, kind: str = "ok") -> RedirectResponse:
    from urllib.parse import urlencode
    return RedirectResponse("/?" + urlencode({"msg": msg, "kind": kind}), status_code=303)


async def _set_action_status(ticket: str, status: str) -> None:
    """Mark pending_actions row(s) for a ticket as approved/declined."""
    pool = await _db_pool()
    if pool is None:
        return
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE pending_actions SET status=$1, resolved_at=NOW(), resolved_by='human' "
                "WHERE ticket_key=$2 AND status='pending'", status, ticket)
    except Exception:
        pass


async def _find_message(ticket: str):
    """Return (message, assignee_name, assignee_id) — prefer pending_actions, else gate."""
    for a in await pending_actions_db():
        if a["ticket"] == ticket:
            return a["message"], a["assignee_name"], a["assignee_id"]
    for g in await pending_gates():
        if g["ticket"] == ticket:
            return _human_message(g["detail"], ticket), "", ""
    return None, "", ""


def _resolve_mention(ticket: str, name: str, aid: str):
    """Return (display_name, accountId) for the @mention. Prefer the ticket's actual
    assignee (carries accountId); else look up by name; else fall back to what we have."""
    if aid and name:
        return name, aid
    try:
        issue = jc.get_issue_detail(ticket)
        if isinstance(issue, dict) and issue.get("assignee_id"):
            return issue.get("assignee") or name, issue["assignee_id"]
    except Exception as e:
        log.warning("mention: get_issue_detail(%s) failed: %s", ticket, e)
    if name:
        try:
            res = jc.find_user(name)
            users = res.get("users", []) if isinstance(res, dict) else []
            if users and users[0].get("accountId"):
                return users[0].get("displayName") or name, users[0]["accountId"]
        except Exception as e:
            log.warning("mention: find_user(%s) failed: %s", name, e)
    return name, aid


@app.post("/approve")
async def approve(ticket: str = Form(...)):
    text, name, aid = await _find_message(ticket)
    if text is None:
        return _flash(f"No pending item found for {ticket} (already resolved?).", "warn")
    name, aid = _resolve_mention(ticket, name, aid)
    try:
        jc.add_comment_adf(ticket, text, name or None, aid or None)
        await _set_action_status(ticket, "approved")
        await _record_resolution(ticket, "approved via UI + posted")
        trust_ledger_log("resolved", f"{ticket} | approved via UI + posted", agent_id="human")
        log.info("Approved + posted to %s via UI", ticket)
        return _flash(f"✓ Comment posted to {ticket} on Jira.", "ok")
    except Exception as e:
        log.error("Approve failed for %s: %s", ticket, e)
        return _flash(f"✗ Failed to post to {ticket}: {e}", "warn")


@app.post("/decline")
async def decline(ticket: str = Form(...)):
    await _set_action_status(ticket, "declined")
    await _record_resolution(ticket, "declined via UI")
    trust_ledger_log("resolved", f"{ticket} | declined via UI", agent_id="human")
    return _flash(f"✗ Declined {ticket}. It will no longer be posted.", "warn")


@app.post("/clear")
async def clear_all():
    """Resolve EVERY pending item (actions + legacy gates) without posting to Jira."""
    n = 0
    # 1. pending_actions table
    pool = await _db_pool()
    if pool is not None:
        try:
            async with pool.acquire() as conn:
                res = await conn.execute(
                    "UPDATE pending_actions SET status='declined', resolved_at=NOW(), "
                    "resolved_by='human-clear-all' WHERE status='pending'")
                n += int(res.split()[-1]) if res and res.split()[-1].isdigit() else 0
        except Exception:
            pass
    # 2. legacy ledger gates — write a resolution for each open ticket
    for g in await pending_gates():
        if g["ticket"]:
            await _record_resolution(g["ticket"], "cleared via UI")
            n += 1
    trust_ledger_log("resolved", f"Cleared {n} pending approvals via UI", agent_id="human")
    log.info("Cleared %d pending approvals via UI", n)
    return _flash(f"🗑 Cleared {n} pending approvals. None were posted to Jira.", "warn")


def _access_token() -> str:
    """ADC access token for the Cloud Run service account (works in Cloud Run)."""
    import google.auth
    from google.auth.transport.requests import Request
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(Request())
    return creds.token


@app.post("/run")
async def run_cycle():
    """Trigger the Cloud Run Job via the Admin API (no gcloud binary needed)."""
    url = (f"https://run.googleapis.com/v2/projects/{PROJECT}/"
           f"locations/{REGION}/jobs/{JOB}:run")
    try:
        token = _access_token()
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(url, headers={"Authorization": f"Bearer {token}"})
            log.info("Job trigger → %s %s", r.status_code, r.text[:200])
        return _flash("▶ Cycle started. Refresh in ~2 min for new results.", "ok")
    except Exception as e:
        log.error("Could not trigger job: %s", e)
        return _flash(f"✗ Could not start cycle: {e}", "warn")


@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True})

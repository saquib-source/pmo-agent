"""
FastAPI HTTP entrypoint for the Events API — the surface the review screen calls.

Each route maps to a Data Contract v1.0 event and delegates to goa/events/api.py,
which reads/writes the real Cloud SQL serving store. Deploy target: Cloud Run.

The screen NEVER touches the database directly; it only calls these endpoints.

Run locally (needs CLOUDSQL_DSN + GCP env):
    uvicorn goa.events.http_app:app --port 8080
"""

from __future__ import annotations

import logging
import pathlib

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import api

log = logging.getLogger(__name__)

app = FastAPI(title="Gross Opportunity Agent — Events API", version="1.0.0")

# Read-only cross-origin access: the BISD org chart (served from
# storage.googleapis.com) live-fetches /api/counts to show real queue numbers.
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

_SCREEN = pathlib.Path(__file__).resolve().parent.parent.parent / "screen" / "index.html"


# ── Request bodies ──────────────────────────────────────────────────────────────
class MarkSeenBody(BaseModel):
    opportunity_id: str
    user_id: str
    via: str  # scroll | opened


class RejectBody(BaseModel):
    opportunity_id: str
    user_id: str
    reason_text: str
    rule_scope: str  # one_time | permanent
    rule_target: str = "initial_screening"


class EditCriteriaBody(BaseModel):
    list_name: str
    operation: str  # add | toggle | remove | update_scope
    rule_or_value: dict


class OppIdBody(BaseModel):
    opportunity_id: str


# ── Health ──────────────────────────────────────────────────────────────────────
# NOTE: bare /healthz is swallowed by the Google Frontend on run.app domains
# (reserved path — returns Google's own 404). Use /api/healthz on Cloud Run.
@app.get("/healthz")
@app.get("/api/healthz")
async def healthz():
    return {"status": "ok"}


# ── Read endpoints ──────────────────────────────────────────────────────────────
@app.get("/api/opportunities")
async def list_opportunities(user_id: str, status: str = "active"):
    return await api.list_opportunities(user_id, status)


@app.get("/api/counts")
async def get_counts(user_id: str):
    return await api.get_counts(user_id)


@app.get("/api/stats")
async def get_stats():
    return await api.get_stats()


@app.get("/api/activity")
async def get_activity(since_id: int = 0, limit: int = 100, agent: str | None = None):
    return await api.get_activity(since_id, limit, agent)


@app.get("/api/agents")
async def get_agents():
    return await api.get_agents()


@app.get("/api/budget")
async def get_budget():
    return await api.get_budget()


@app.get("/api/opportunities/{opportunity_id}")
async def open_detail(opportunity_id: str):
    row = await api.open_detail(opportunity_id)
    if row is None:
        raise HTTPException(status_code=404, detail="opportunity not found")
    return row


# ── Write endpoints (contract events) ───────────────────────────────────────────
@app.post("/api/opportunities/{opportunity_id}/full_report")
async def pull_full_report(opportunity_id: str):
    return await api.pull_full_report(opportunity_id)


@app.post("/api/seen")
async def mark_seen(body: MarkSeenBody):
    await api.mark_seen(body.opportunity_id, body.user_id, body.via)
    return {"ok": True}


@app.post("/api/reject")
async def reject_opportunity(body: RejectBody):
    await api.reject_opportunity(
        body.opportunity_id, body.user_id, body.reason_text, body.rule_scope, body.rule_target
    )
    return {"ok": True}


@app.post("/api/reopen")
async def reopen_opportunity(body: OppIdBody):
    await api.reopen_opportunity(body.opportunity_id)
    return {"ok": True}


@app.post("/api/criteria")
async def edit_criteria(body: EditCriteriaBody):
    await api.edit_criteria(body.list_name, body.operation, body.rule_or_value)
    return {"ok": True}


# ── Review screen ───────────────────────────────────────────────────────────────
@app.get("/")
async def screen():
    if _SCREEN.exists():
        return FileResponse(str(_SCREEN))
    return JSONResponse(
        {"detail": "review screen not built yet; API is live at /api/*"}, status_code=200
    )

#!/usr/bin/env python3
"""
Email a leads report (xlsx) from the GOA serving store via SendGrid.

Reads the opportunity table exactly as-is — every cell in the report is a
value from the store; unknown fields stay blank (Gap per Data Contract v1.0).

Run:  .venv/bin/python scripts/email_leads_report.py --to saquib@isrdsystems.com
Env:  CLOUDSQL_DSN            serving-store DSN
      SENDGRID_API_KEY        SendGrid key (secret goa-sendgrid-api-key)
      SENDGRID_FROM_EMAIL     verified sender (support@ashs.ai)
Dep:  asyncpg, openpyxl
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import ssl
import urllib.request
from datetime import datetime, timezone

import asyncpg
import openpyxl
from openpyxl.styles import Font

COLUMNS = [
    ("project_name", "Project"),
    ("source_name", "Source"),
    ("owner", "GC / Buyer"),
    ("city", "City"),
    ("state", "State"),
    ("record_type", "Record Type"),
    ("bid_date", "Bid Due"),
    ("valuation", "Valuation"),
    ("gate_passed", "Gate"),
    ("gate_score", "Score"),
    ("status", "Status"),
    ("first_seen_at", "First Seen (UTC)"),
]

QUERY = """
SELECT o.project_name, o.owner, o.city, o.state, o.record_type, o.bid_date,
       o.valuation, o.gate_passed, o.gate_score, o.status, o.first_seen_at,
       (SELECT string_agg(DISTINCT sl.source_name, ' + ')
          FROM source_link sl WHERE sl.opportunity_id = o.opportunity_id) AS source_name
FROM opportunity o
ORDER BY o.first_seen_at DESC, o.project_name
"""


async def fetch_rows(dsn: str) -> list[dict]:
    conn = await asyncpg.connect(dsn)
    try:
        return [dict(r) for r in await conn.fetch(QUERY)]
    finally:
        await conn.close()


def build_xlsx(rows: list[dict]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Opportunities"
    ws.append([label for _, label in COLUMNS])
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for r in rows:
        ws.append([
            ("kept" if r[k] else "flagged") if k == "gate_passed"
            else (r[k].isoformat() if k in ("bid_date",) and r[k] else
                  r[k].strftime("%Y-%m-%d %H:%M") if k == "first_seen_at" and r[k] else r[k])
            for k, _ in COLUMNS
        ])
    for col in ws.columns:
        width = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[col[0].column_letter].width = min(52, width + 2)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def send(xlsx: bytes, to: str, n_rows: int) -> None:
    key = os.environ["SENDGRID_API_KEY"]
    sender = os.environ.get("SENDGRID_FROM_EMAIL", "support@ashs.ai")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    fname = f"GOA_Leads_Report_{today}.xlsx"
    body = {
        "personalizations": [{"to": [{"email": to}]}],
        "from": {"email": sender, "name": "Gross Opportunity Agent"},
        "subject": f"GOA leads report — {n_rows} opportunities in the review queue ({today})",
        "content": [{
            "type": "text/plain",
            "value": (
                f"Attached: {n_rows} opportunities currently in the Gross Opportunity "
                f"serving store (all sources), exported {today} UTC.\n\n"
                "Every value is real store data; blank cells are unknowns kept null "
                "per Data Contract v1.0.\n\n"
                "Console: https://goa-console-1059272334202.us-central1.run.app/\n"
            ),
        }],
        "attachments": [{
            "content": base64.b64encode(xlsx).decode(),
            "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "filename": fname,
        }],
    }
    req = urllib.request.Request(
        "https://api.sendgrid.com/v3/mail/send",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST",
    )
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or None)
    with urllib.request.urlopen(req, context=ctx) as resp:
        print(f"SendGrid: HTTP {resp.status} — sent {fname} ({n_rows} rows) to {to}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--to", required=True)
    args = ap.parse_args()
    rows = asyncio.run(fetch_rows(os.environ["CLOUDSQL_DSN"]))
    send(build_xlsx(rows), args.to, len(rows))


if __name__ == "__main__":
    main()

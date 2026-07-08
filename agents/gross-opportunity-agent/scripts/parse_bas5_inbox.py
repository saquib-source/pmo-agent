#!/usr/bin/env python3
"""
Parse Meri's emailed bid-leads workbook (BAS-5 attachment
ISRDS_Bid_Requests_Inbox.xlsx, sheet "Bid Requests") into raw records for the
`emailed_bid_leads` file-drop source.

Honesty rules (Data Contract v1.0 — enforced here, per Todd's hard line):
- Every value is copied VERBATIM from Meri's sheet; nothing is invented.
- A field that cannot be resolved mechanically stays ABSENT so the canonical
  field lands null ("Gap"). Bid due dates like "Check RFP on portal" are kept
  as text only; `bid_due_iso` is emitted ONLY when the cell parses as a real
  date. Location is split into city/state ONLY for an exact "City, ST" match.
- record_type is "itb" for every row: the sheet is bid requests / invitations
  forwarded by the Group 1 portals (same convention as AlertEmailAdapter).
- Records are DETERMINISTIC (no timestamps, stable ids) so the orchestrator's
  fired_marker makes re-runs idempotent.

Run:  .venv/bin/python scripts/parse_bas5_inbox.py
Out:  config/sources/data/emailed_bid_leads.json  (ships in the image)
Dep:  openpyxl
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re
from datetime import datetime

import openpyxl

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent
XLSX = HERE / "data" / "ISRDS_Bid_Requests_Inbox.xlsx"
OUT = REPO / "config" / "sources" / "data" / "emailed_bid_leads.json"

CITY_ST = re.compile(r"^(.*?),\s*([A-Za-z]{2})\.?$")
DATE_PREFIX = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})")


def _txt(v) -> str | None:
    """Cell → stripped text, or None. Excel may hand back datetimes."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.strftime("%-m/%-d/%Y")
    s = str(v).strip()
    return s or None


def _iso_date(v) -> str | None:
    """ISO date ONLY when the cell starts with a real M/D/YYYY (or is a datetime).
    Anything else ("Check RFP on portal", …) → None; the text is kept separately."""
    if isinstance(v, datetime):
        return v.date().isoformat()
    s = _txt(v)
    if not s:
        return None
    m = DATE_PREFIX.match(s)
    if not m:
        return None
    mm, dd, yyyy = (int(x) for x in m.groups())
    try:
        return datetime(yyyy, mm, dd).date().isoformat()
    except ValueError:
        return None


def _city_state(v) -> tuple[str | None, str | None]:
    s = _txt(v)
    if not s or s.upper() in ("N/A", "NA", "TBD", "-"):
        return None, None
    m = CITY_ST.match(s)
    if m and m.group(1).strip():
        return m.group(1).strip(), m.group(2).upper()
    return s, None  # keep whatever Meri wrote as the city; state stays Gap


def main() -> None:
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb["Bid Requests"]
    rows = list(ws.iter_rows(values_only=True))
    header = [str(h) for h in rows[0]]

    records = []
    for row in rows[1:]:
        if not any(c is not None and str(c).strip() for c in row):
            continue
        original = {header[i]: _txt(row[i]) for i in range(min(len(header), len(row)))}

        project = original.get("Project / Solicitation Name")
        if not project:
            continue  # a lead without a project name is not a record

        city, state = _city_state(original.get("Location"))
        rec = {
            # identity: stable content hash — re-parsing or re-running never dupes
            "source_record_id": "BAS5-" + hashlib.sha1(
                "|".join([
                    project,
                    original.get("GC / Buyer Company") or "",
                    original.get("Scope of Work") or "",
                    original.get("Portal/Source") or "",
                ]).encode()
            ).hexdigest()[:16],
            "project_name": project,
            "owner_company": original.get("GC / Buyer Company"),
            "city": city,
            "state": state,
            # description feeds CSI keyword extraction + the coarse gate — verbatim scope
            "description": original.get("Scope of Work"),
            "record_type": "itb",  # bid requests / portal invitations, per sheet title
            "bid_due_iso": _iso_date(original.get("Bid Due Date")),
            "bid_due_text": original.get("Bid Due Date"),
            "portal": original.get("Portal/Source"),
            "sender_email": original.get("Contact / Sender Email"),
            "date_received": _iso_date(original.get("Date Received")),
            "inbox_status": original.get("Status"),
            "inbox_notes": original.get("Notes"),
            "original": original,  # full verbatim row for the lake
        }
        records.append(rec)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(records, indent=1, ensure_ascii=False), encoding="utf-8")

    with_date = sum(1 for r in records if r["bid_due_iso"])
    with_loc = sum(1 for r in records if r["city"])
    print(f"{len(records)} records → {OUT.relative_to(REPO)}")
    print(f"  bid_due parsed: {with_date} (rest stay null — 'check portal' text kept as text)")
    print(f"  location present: {with_loc}")
    portals = {}
    for r in records:
        portals[r["portal"]] = portals.get(r["portal"], 0) + 1
    print(f"  by portal: {portals}")


if __name__ == "__main__":
    main()

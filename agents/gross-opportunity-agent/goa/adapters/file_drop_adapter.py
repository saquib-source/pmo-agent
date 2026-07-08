"""
File-drop adapter — for lead lists that arrive as files, not feeds: emailed
bid invitations transcribed to a tracker (BAS-5), one-off CSV/XLSX exports
from portals with no API, etc.

A parser script (scripts/) converts the file to deterministic raw-record JSON
under config/sources/data/<source_id>.json; that JSON ships in the image and
this adapter simply yields the records. Idempotency is the orchestrator's
fired_marker (stable content hash), so re-running the same drop never dupes.

No HTTP requests are made, so no request budget is consumed.
"""

import json
import logging
import pathlib
from typing import Iterator

from .base import Adapter

log = logging.getLogger(__name__)

_APP_ROOT = pathlib.Path(__file__).resolve().parents[2]


class FileDropAdapter(Adapter):
    def pull(self, mode: str, watermark: str | None) -> Iterator[dict]:
        rel = self.cfg.get("records_path")
        if not rel:
            log.error("FileDropAdapter %s: config is missing records_path", self.source_id)
            return
        path = (_APP_ROOT / rel).resolve()
        if not path.is_file():
            log.error("FileDropAdapter %s: records file not found: %s", self.source_id, path)
            return
        records = json.loads(path.read_text(encoding="utf-8"))
        log.info("FileDropAdapter %s: %d record(s) in %s", self.source_id, len(records), rel)
        for rec in records:
            yield rec

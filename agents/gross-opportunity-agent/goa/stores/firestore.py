"""
Firestore store — append-only activity stream.
Collection: live/projects/gross_opportunity/activity/{event_id}
The review screen subscribes to this for the live activity ticker.
Gap: GCP project id.
"""

import logging
import os
from datetime import datetime
from typing import Literal

log = logging.getLogger(__name__)

# Gap: set via environment
_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
_COLLECTION = "live/projects/gross_opportunity/activity"

ActivityType = Literal["pulled", "normalized", "deduped", "gated_kept", "gated_dropped", "forked", "source_silent"]


def _client():
    from google.cloud import firestore  # type: ignore
    return firestore.AsyncClient(project=_PROJECT)


async def log_activity(
    event_type: ActivityType,
    source_id: str,
    message: str,
    opportunity_id: str | None = None,
) -> None:
    """Append one activity event to the stream. Non-blocking best-effort."""
    try:
        client = _client()
        doc = client.collection(_COLLECTION).document()
        await doc.set({
            "ts": datetime.utcnow(),
            "type": event_type,
            "source": source_id,
            "message": message,
            "opportunity_id": opportunity_id,
        })
    except Exception as e:
        log.warning("Firestore activity write failed: %s", e)

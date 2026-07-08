"""
Firestore store — append-only activity stream.
Collection: projects/gross_opportunity/activity (docs = {event_id})
The review screen subscribes to this for the live activity ticker.
Gap: GCP project id.
"""

import logging
import os
from datetime import datetime
from typing import Literal

log = logging.getLogger(__name__)

_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT")
# Firestore collection paths must have an ODD number of segments
# (collection/doc/collection). The previous 4-segment path was rejected on
# every write ("A collection must have an odd number of path elements").
_COLLECTION = os.environ.get("GOA_ACTIVITY_COLLECTION", "projects/gross_opportunity/activity")
# GOA_SKIP_ACTIVITY=1 disables the Firestore ticker (for local runs where the
# Firestore gRPC client is proxy-blocked). The activity stream is a UI nicety, not
# part of the serving path. Unset in production (Cloud Run).
_SKIP = os.environ.get("GOA_SKIP_ACTIVITY") == "1"

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
    if _SKIP:
        return
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

"""
Idempotency — the atomic commit_record that prevents double-writing.
See Section 8 of the build spec. The unique PK on fired_marker is the guarantee.
"""

import hashlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)


def stable_hash(source_id: str, raw: dict) -> str:
    """SHA-256 of the source + canonical JSON of the raw record.
    The hash must be stable: same raw payload always produces the same hash.
    """
    canonical_json = json.dumps(raw, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(f"{source_id}:{canonical_json}".encode()).hexdigest()


async def commit_record(source_id: str, record_hash: str, opp: Any, upsert_fn) -> Any | None:
    """Atomic check-and-commit. Returns the opportunity on new write, None if already fired.

    upsert_fn is a coroutine: async (CanonicalOpportunity) -> CanonicalOpportunity
    The fired_marker insert and the opportunity upsert happen in one transaction in cloudsql.
    """
    from ..stores import cloudsql

    is_new = await cloudsql.check_and_mark_fired(source_id, record_hash, opp.opportunity_id)
    if not is_new:
        log.debug("Idempotency: already fired source=%s hash=%s, skipping", source_id, record_hash[:12])
        return None

    result = await upsert_fn(opp)
    log.debug("Idempotency: committed source=%s hash=%s opp=%s", source_id, record_hash[:12], opp.opportunity_id)
    return result

"""
Vertex AI Agent Engine memory — long-term agent memory.
Used lightly in GOA: mainly for scout reasoning context and inter-run continuity.
Gap: Vertex AI Agent Engine resource name.
"""

import logging
import os

log = logging.getLogger(__name__)

_ENGINE_ID = os.environ.get("VERTEX_AGENT_ENGINE_ID", "")
_PROJECT = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
_LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")


async def remember(key: str, value: str) -> None:
    """Store a key-value pair in agent long-term memory. Best-effort."""
    if not _ENGINE_ID:
        log.debug("Agent memory: ENGINE_ID not set, skipping remember(%s)", key)
        return
    try:
        from google.cloud import aiplatform  # type: ignore
        # Stub: replace with actual Agent Engine memory API when available
        log.info("Agent memory: remember key=%s", key)
    except Exception as e:
        log.warning("Agent memory: remember failed: %s", e)


async def recall(key: str) -> str | None:
    """Retrieve a value from agent long-term memory."""
    if not _ENGINE_ID:
        return None
    try:
        from google.cloud import aiplatform  # type: ignore
        log.info("Agent memory: recall key=%s", key)
        return None  # Stub
    except Exception as e:
        log.warning("Agent memory: recall failed: %s", e)
        return None

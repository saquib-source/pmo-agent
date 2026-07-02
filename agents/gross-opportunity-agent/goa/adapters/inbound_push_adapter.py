"""
Inbound push adapter — for GC invitation networks.
Receives invitations the general contractors push to us by webhook or mailbox,
yields them as record_type itb. We do not scan these networks.
Gap: webhook path and signature verification per source.
"""

import logging
from typing import Iterator

from .base import Adapter

log = logging.getLogger(__name__)


class InboundPushAdapter(Adapter):
    """
    Pull mode is not applicable here — push events arrive via webhook.
    This adapter's pull() is a drain of a short-lived queue populated by the webhook handler.
    The webhook handler lives in events/api.py and enqueues raw dicts.
    """

    _queue: list[dict] = []  # Simple in-process queue; replace with Cloud Tasks for production

    @classmethod
    def enqueue(cls, payload: dict) -> None:
        cls._queue.append(payload)

    def pull(self, mode: str, watermark: str | None) -> Iterator[dict]:
        while self._queue:
            raw = self._queue.pop(0)
            raw.setdefault("_record_type", "itb")
            yield raw

"""
Adapter interface — every source adapter implements this contract.
A new clean source is a config file plus a one-line method registration, not new code.
"""

from abc import ABC, abstractmethod
from typing import Any, Iterator


class Adapter(ABC):
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.source_id: str = cfg["source_id"]
        self._cursor: Any = None

    @abstractmethod
    def pull(self, mode: str, watermark: str | None) -> Iterator[dict]:
        """Yield raw records as dicts.
        mode: 'backfill' — read everything, throttled to rate_limit.
        mode: 'delta'    — read only what changed since watermark.
        """

    @property
    def cursor(self) -> Any:
        """The new watermark value after a pull, or None if the source has no cursor."""
        return self._cursor

    def fetch_full(self, source_record_id: str) -> tuple[dict, list[dict]]:
        """Pull the full record and its spec variants on demand.
        Returns (full_record_dict, list_of_spec_variant_dicts).
        Default raises NotImplementedError — override only for sources that support it.
        """
        raise NotImplementedError(f"{self.__class__.__name__} does not support fetch_full")

    def _rate_limit_sleep(self) -> None:
        """Pause to respect the source rate limit."""
        import time
        rps = (self.cfg.get("rate_limit") or {}).get("requests_per_second", 1)
        if rps > 0:
            time.sleep(1 / rps)

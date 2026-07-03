"""
Adapter interface — every source adapter implements this contract.
A new clean source is a config file plus a one-line method registration, not new code.
"""

from abc import ABC, abstractmethod
from typing import Any, Iterator


class RateLimited(Exception):
    """Raised when a source rate-limits (HTTP 429). The orchestrator stops the pull
    cleanly and keeps whatever was already processed; the next run resumes."""


class BudgetExhausted(Exception):
    """Raised when the adapter's own daily request budget is used up BEFORE the source
    can 429 us. The orchestrator stops the pull cleanly (like RateLimited) and the next
    run — with a fresh daily budget — resumes the same window."""


class Adapter(ABC):
    def __init__(self, cfg: dict) -> None:
        self.cfg = cfg
        self.source_id: str = cfg["source_id"]
        self._cursor: Any = None
        # Request-budget accounting. The orchestrator sets the per-run budget from the
        # persisted daily ledger; the adapter counts every HTTP request it makes and
        # raises BudgetExhausted instead of letting the source 429 us.
        self._requests_made: int = 0
        self._request_budget: int | None = None  # None = unlimited

    @property
    def requests_made(self) -> int:
        """HTTP requests actually made during this run (persisted to the ledger)."""
        return self._requests_made

    def set_request_budget(self, budget: int | None) -> None:
        """Max HTTP requests this run may make. None = unlimited."""
        self._request_budget = budget

    def _charge_request(self) -> None:
        """Count one outbound request against the budget. Call BEFORE the request.
        Raises BudgetExhausted when the budget is already spent."""
        if self._request_budget is not None and self._requests_made >= self._request_budget:
            raise BudgetExhausted(
                f"{self.source_id}: daily request budget spent "
                f"({self._requests_made}/{self._request_budget} this run)"
            )
        self._requests_made += 1

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

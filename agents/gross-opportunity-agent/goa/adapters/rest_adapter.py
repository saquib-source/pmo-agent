"""
Generic REST adapter — offset/cursor pagination, api_key or bearer auth.
Source-specific behaviour is driven entirely by the source config file.
"""

import logging
import os
from typing import Any, Iterator

import requests  # type: ignore

from .base import Adapter, RateLimited

log = logging.getLogger(__name__)


def _session() -> requests.Session:
    """A requests Session. If GOA_FORCE_IPV4=1, pin outbound connections to IPv4 —
    needed on networks where the IPv6 path to a source (e.g. api.sam.gov) is broken
    while IPv4 works. No effect on Cloud Run (dual-stack healthy)."""
    s = requests.Session()
    if os.environ.get("GOA_FORCE_IPV4") == "1":
        import socket
        import urllib3.util.connection as urllib3_cn  # type: ignore

        def _allowed_gai_family():
            return socket.AF_INET

        urllib3_cn.allowed_gai_family = _allowed_gai_family
    return s


def _load_secret(secret_ref: str, env_var: str | None = None) -> str:
    """Resolve a credential. An env var wins (for local runs where the Secret Manager
    gRPC client is blocked by a proxy); otherwise read from Secret Manager."""
    if env_var and os.environ.get(env_var):
        return os.environ[env_var]
    from google.cloud import secretmanager  # type: ignore
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=secret_ref)
    return response.payload.data.decode()


class RestAdapter(Adapter):
    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        auth = cfg.get("auth", {})
        # Config may name an env var to source the secret from locally (auth.env_var).
        secret = ""
        if auth.get("secret_ref") or auth.get("env_var"):
            secret = _load_secret(auth.get("secret_ref", ""), auth.get("env_var"))
        self._headers = {}
        self._auth_query = {}  # api key carried as a query param (e.g. SAM.gov ?api_key=)
        atype = auth.get("type")
        if atype == "api_key":
            self._headers = {auth.get("header", "Authorization"): secret}
        elif atype == "bearer":
            self._headers = {"Authorization": f"Bearer {secret}"}
        elif atype == "query_key":
            self._auth_query = {auth.get("param", "api_key"): secret}

        self._sess = _session()

    def extra_params(self, mode: str, watermark: str | None) -> dict:
        """Hook for source-specific required params (e.g. SAM.gov date window).
        Override in a subclass. Default: none."""
        return {}

    def param_variants(self, mode: str, watermark: str | None) -> list[dict]:
        """One dict per search the pull should run (each variant is its own paginated
        loop). Default: a single search using extra_params(). Sources with a query
        plan (multiple keywords / NAICS codes) override this — every variant still
        shares the same run-level request budget, so more variants never means 429."""
        return [self.extra_params(mode, watermark)]

    def _get(self, url: str, params: dict) -> "requests.Response":
        """One budget-charged, throttled GET. Raises BudgetExhausted before the
        request when the daily budget is spent, RateLimited on a source 429."""
        self._charge_request()
        self._rate_limit_sleep()
        resp = self._sess.get(url, headers=self._headers, params=params, timeout=30)
        if resp.status_code == 429:
            # Rate limited by the source. Stop this pull cleanly — the run keeps
            # whatever it already yielded; the next scheduled run resumes.
            retry_after = resp.headers.get("Retry-After", "?")
            raise RateLimited(f"{url} rate-limited (429); Retry-After={retry_after}")
        resp.raise_for_status()
        return resp

    def pull(self, mode: str, watermark: str | None) -> Iterator[dict]:
        for variant in self.param_variants(mode, watermark):
            yield from self._pull_one_search(mode, watermark, variant)
        self._cursor = self._advanced_cursor(mode, watermark)

    def _advanced_cursor(self, mode: str, watermark: str | None):
        """New watermark after a fully-successful pull. Default keeps legacy behavior."""
        return watermark

    def _pull_one_search(self, mode: str, watermark: str | None, variant: dict) -> Iterator[dict]:
        pagination = self.cfg.get("pagination", {})
        style = pagination.get("style", "offset")
        base_url = self.cfg["base_url"]
        params = dict(self.cfg.get("query_params") or {})
        params.update(self._auth_query)  # inject api_key query param if configured
        params.update(variant)           # source-specific required params for this search
        # Drop comment/gap placeholder keys (config uses _-prefixed markers).
        params = {k: v for k, v in params.items() if not str(k).startswith("_")}

        if mode == "delta" and watermark and self.cfg.get("watermark_field"):
            params[self.cfg["watermark_field"]] = watermark

        offset = 0
        cursor_token = None
        while True:
            if style == "offset":
                params[pagination.get("offset_param", "offset")] = offset
            elif style == "cursor" and cursor_token:
                params[pagination.get("cursor_field", "cursor")] = cursor_token

            resp = self._get(base_url, params)
            data = resp.json()

            records = data if isinstance(data, list) else data.get("opportunitiesData") or data.get("results") or data.get("items") or []
            if not records:
                break

            for rec in records:
                yield rec

            if style == "offset":
                offset += len(records)
                total = data.get(pagination.get("total_field", "total"))
                if total and offset >= total:
                    break
                if len(records) < int(params.get("limit", 100)):
                    break
            elif style == "cursor":
                cursor_token = data.get(pagination.get("cursor_field", "nextToken"))
                if not cursor_token:
                    break
            else:
                break

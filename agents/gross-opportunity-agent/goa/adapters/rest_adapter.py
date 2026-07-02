"""
Generic REST adapter — offset/cursor pagination, api_key or bearer auth.
Source-specific behaviour is driven entirely by the source config file.
"""

import logging
from typing import Any, Iterator

import requests  # type: ignore

from .base import Adapter

log = logging.getLogger(__name__)


def _load_secret(secret_ref: str) -> str:
    from google.cloud import secretmanager  # type: ignore
    client = secretmanager.SecretManagerServiceClient()
    response = client.access_secret_version(name=secret_ref)
    return response.payload.data.decode()


class RestAdapter(Adapter):
    def __init__(self, cfg: dict) -> None:
        super().__init__(cfg)
        auth = cfg.get("auth", {})
        secret = _load_secret(auth["secret_ref"]) if auth.get("secret_ref") else ""
        if auth.get("type") == "api_key":
            self._headers = {auth.get("header", "Authorization"): secret}
        elif auth.get("type") == "bearer":
            self._headers = {"Authorization": f"Bearer {secret}"}
        else:
            self._headers = {}

    def pull(self, mode: str, watermark: str | None) -> Iterator[dict]:
        pagination = self.cfg.get("pagination", {})
        style = pagination.get("style", "offset")
        base_url = self.cfg["base_url"]
        params = dict(self.cfg.get("query_params") or {})

        if mode == "delta" and watermark and self.cfg.get("watermark_field"):
            params[self.cfg["watermark_field"]] = watermark

        offset = 0
        while True:
            if style == "offset":
                params[pagination.get("offset_param", "offset")] = offset
            elif style == "cursor" and self._cursor:
                params[pagination.get("cursor_field", "cursor")] = self._cursor

            self._rate_limit_sleep()
            resp = requests.get(base_url, headers=self._headers, params=params, timeout=30)
            resp.raise_for_status()
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
                if len(records) < params.get("limit", 100):
                    break
            elif style == "cursor":
                next_token = data.get(pagination.get("cursor_field", "nextToken"))
                if not next_token:
                    break
                self._cursor = next_token
            else:
                break

        self._cursor = params.get(self.cfg.get("watermark_field", ""), watermark)

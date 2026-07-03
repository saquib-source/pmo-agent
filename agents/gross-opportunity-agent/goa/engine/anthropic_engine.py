"""
Direct Anthropic API transport for Claude models.

Used when config/engines.json sets `_transport: anthropic` — the first-party Claude
API over HTTPS (the requests/httpx path), which works through environments where the
gRPC Vertex path does not (e.g. a TLS-inspecting corporate proxy on a dev laptop).

The model id and effort come from the Config Registry binding, never hardcoded here.
Auth: ANTHROPIC_API_KEY (or an `ant auth login` profile).

Fable-family models: thinking is always on (the `thinking` param is omitted),
depth is controlled with output_config.effort, and sampling params are not sent.
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic  # type: ignore
        # Resolves ANTHROPIC_API_KEY, ANTHROPIC_AUTH_TOKEN, or an ant-login profile.
        _client = anthropic.Anthropic()
        log.info("Anthropic engine: client ready")
    return _client


def _build_kwargs(binding: dict, system: str, user_content) -> dict:
    model = binding["version"]
    kwargs: dict = {
        "model": model,
        "max_tokens": int(binding.get("max_tokens", 1024)),
        "messages": [{"role": "user", "content": user_content}],
    }
    if system:
        kwargs["system"] = system

    effort = binding.get("effort")
    if effort:
        kwargs["output_config"] = {"effort": effort}

    family = (binding.get("family") or "").lower()
    if family in ("fable", "mythos"):
        # Fable requires opting into a server-side refusal fallback so a benign
        # false-positive refusal doesn't fail the call outright.
        kwargs["betas"] = ["server-side-fallback-2026-06-01"]
        kwargs["fallbacks"] = [{"model": "claude-opus-4-8"}]
    else:
        kwargs["thinking"] = {"type": "adaptive"}
    return kwargs


def _create(kwargs: dict):
    client = _get_client()
    # Fable path uses the beta messages endpoint (server-side fallbacks).
    if "betas" in kwargs:
        return client.beta.messages.create(**kwargs)
    return client.messages.create(**kwargs)


def run(binding: dict, system: str, user_content) -> str:
    resp = _create(_build_kwargs(binding, system, user_content))
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Model refused request (stop_details={getattr(resp, 'stop_details', None)})")
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def run_json(binding: dict, system: str, user_content, schema: dict) -> dict:
    kwargs = _build_kwargs(binding, system, user_content)
    oc = kwargs.get("output_config", {})
    oc["format"] = {"type": "json_schema", "schema": schema}
    kwargs["output_config"] = oc
    resp = _create(kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Model refused request (stop_details={getattr(resp, 'stop_details', None)})")
    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    return json.loads(text)

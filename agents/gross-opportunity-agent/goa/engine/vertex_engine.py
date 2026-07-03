"""
Vertex AI transport for Claude models.

Calls Claude on Google Cloud Vertex AI via GCP Application Default Credentials —
the same project and auth as the data stores, no separate API key. The model id
and effort come from the Config Registry binding, never hardcoded here.

Requires:  anthropic[vertex]
Auth:      GCP ADC (gcloud auth application-default login, or a service account)
Env:       GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION (region; default "us-central1")

Fable-family models: thinking is always on (the `thinking` param is omitted),
depth is controlled with output_config.effort, and sampling params are not sent.
This module builds the request from the binding and does not special-case any
model name beyond the family conventions the API itself enforces.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

_client = None


def _get_client():
    """Lazily construct the AnthropicVertex client from GCP env. Real call, no stub."""
    global _client
    if _client is None:
        from anthropic import AnthropicVertex  # type: ignore

        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        region = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
        if not project:
            raise RuntimeError(
                "GOOGLE_CLOUD_PROJECT is not set — cannot reach Vertex AI. "
                "Set it (and GOOGLE_CLOUD_LOCATION) and ensure ADC is configured."
            )
        _client = AnthropicVertex(project_id=project, region=region)
        log.info("Vertex engine: client ready project=%s region=%s", project, region)
    return _client


def _build_kwargs(binding: dict, system: str, user_content: Any) -> dict:
    """Assemble messages.create kwargs from a Config Registry binding.

    binding carries: version (model id), effort, max_tokens. The family determines
    thinking handling — for the fable family (always-on thinking) the thinking param
    is omitted; effort controls depth.
    """
    model = binding["version"]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": int(binding.get("max_tokens", 1024)),
        "messages": [{"role": "user", "content": user_content}],
    }
    if system:
        kwargs["system"] = system

    effort = binding.get("effort")
    if effort:
        kwargs["output_config"] = {"effort": effort}

    # Fable family: thinking is always on and configured by the server — omit the
    # `thinking` param entirely (an explicit value is rejected). Opus/Sonnet-tier
    # bindings can opt into adaptive thinking explicitly.
    family = (binding.get("family") or "").lower()
    if family not in ("fable", "mythos"):
        kwargs["thinking"] = {"type": "adaptive"}

    return kwargs


def run(binding: dict, system: str, user_content: Any) -> str:
    """Run one model call and return the concatenated text of the response.

    Raises on transport/model error — there is no recall-first fallback here;
    callers decide their own recall-first behaviour on exception.
    """
    client = _get_client()
    kwargs = _build_kwargs(binding, system, user_content)
    resp = client.messages.create(**kwargs)

    if resp.stop_reason == "refusal":
        # Fable safety classifier declined. Surface it — the caller's recall-first
        # policy decides what to do. Do not fabricate an answer.
        raise RuntimeError(f"Model refused request (stop_details={getattr(resp, 'stop_details', None)})")

    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


def run_json(binding: dict, system: str, user_content: Any, schema: dict) -> dict:
    """Run one model call constrained to a JSON schema; return the parsed object.

    Uses output_config.format (structured outputs). Raises on refusal or parse failure.
    """
    client = _get_client()
    kwargs = _build_kwargs(binding, system, user_content)
    oc = kwargs.get("output_config", {})
    oc["format"] = {"type": "json_schema", "schema": schema}
    kwargs["output_config"] = oc

    resp = client.messages.create(**kwargs)
    if resp.stop_reason == "refusal":
        raise RuntimeError(f"Model refused request (stop_details={getattr(resp, 'stop_details', None)})")

    text = next((b.text for b in resp.content if getattr(b, "type", None) == "text"), "")
    return json.loads(text)

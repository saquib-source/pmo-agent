"""
run_role — the one entry point function code uses to invoke a model.

Resolves the ROLE's engine binding from the Config Registry, reads the configured
transport (engines.json `_transport`), and dispatches. Function code passes a role
string and a prompt; it never sees a model id or a vendor SDK.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from ..stores.config_registry import resolve

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "engines.json"
_transport_cache: str | None = None


def _transport() -> str:
    """Resolve the transport. GOA_ENGINE_TRANSPORT env wins (for local runs where the
    gRPC Vertex path is blocked and the direct 'anthropic' HTTPS path works); otherwise
    the committed engines.json `_transport` (production default: vertex)."""
    override = os.environ.get("GOA_ENGINE_TRANSPORT")
    if override:
        return override
    global _transport_cache
    if _transport_cache is None:
        try:
            with open(_CONFIG_PATH) as f:
                raw = json.load(f)
            _transport_cache = raw.get("_transport", "vertex")
        except FileNotFoundError:
            _transport_cache = "vertex"
    return _transport_cache


def _dispatch():
    t = _transport()
    if t == "vertex":
        from . import vertex_engine
        return vertex_engine
    if t == "anthropic":
        from . import anthropic_engine  # optional alt transport
        return anthropic_engine
    raise ValueError(f"Unknown engine transport '{t}' in engines.json")


def _apply_overrides(binding: dict) -> dict:
    """Local-only engine overrides via env, so the committed engines.json (production
    binding: claude-fable-5 / vertex) is never edited for a dev run. Set
    GOA_ENGINE_OVERRIDE_VERSION (e.g. claude-opus-4-8) and optionally
    GOA_ENGINE_OVERRIDE_FAMILY to run a cheaper model locally."""
    ver = os.environ.get("GOA_ENGINE_OVERRIDE_VERSION")
    if not ver:
        return binding
    b = dict(binding)
    b["version"] = ver
    b["family"] = os.environ.get("GOA_ENGINE_OVERRIDE_FAMILY", "opus")
    return b


def run_role(role: str, system: str, user_content: Any) -> str:
    """Invoke the model bound to `role`. Returns response text. Raises on error."""
    binding = _apply_overrides(resolve(role))
    log.info("engine.run_role role=%s engine=%s %s transport=%s",
             role, binding.get("family"), binding.get("version"), _transport())
    return _dispatch().run(binding, system, user_content)


def run_role_json(role: str, system: str, user_content: Any, schema: dict) -> dict:
    """Invoke the model bound to `role`, constrained to a JSON schema. Raises on error."""
    binding = _apply_overrides(resolve(role))
    log.info("engine.run_role_json role=%s engine=%s %s transport=%s",
             role, binding.get("family"), binding.get("version"), _transport())
    return _dispatch().run_json(binding, system, user_content, schema)

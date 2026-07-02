"""
Config Registry — resolves role to engine binding at runtime.
Reads config/engines.json. Never exposes a vendor or model string from application code.

Usage:
    from goa.stores.config_registry import resolve
    engine = resolve("gate_classifier")   # -> {"family": "fable", "version": "claude-fable-5", ...}
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "engines.json"
_cache: dict[str, Any] = {}


def _load() -> dict[str, Any]:
    global _cache
    if _cache:
        return _cache
    try:
        with open(_CONFIG_PATH) as f:
            raw = json.load(f)
        _cache = {k: v for k, v in raw.items() if not k.startswith("_")}
        log.info("Config Registry: loaded %d roles from %s", len(_cache), _CONFIG_PATH)
    except FileNotFoundError:
        log.error("Config Registry: engines.json not found at %s", _CONFIG_PATH)
        _cache = {}
    return _cache


def resolve(role: str) -> dict[str, Any]:
    """Return the engine binding dict for a role. Raises KeyError if role is unknown."""
    registry = _load()
    if role not in registry:
        raise KeyError(f"Config Registry: unknown role '{role}'. Available: {list(registry)}")
    return registry[role]


def get_version(role: str) -> str:
    return resolve(role)["version"]


def get_family(role: str) -> str:
    return resolve(role)["family"]


def get_tools(role: str) -> list[str]:
    return resolve(role).get("tools", [])

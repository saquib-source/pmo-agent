"""
Engine layer — the runtime bridge from a function ROLE to a live model call.

Function code never names a vendor or model. It calls engine.run_role(role, ...),
which resolves the engine binding from the Config Registry (config/engines.json)
and dispatches to the transport named by engines.json `_transport` (currently vertex).

This is the single place a model is actually invoked. Swapping transport or model
is a config edit, never a code edit — the Golden Rule holds.
"""

from .runner import run_role, run_role_json

__all__ = ["run_role", "run_role_json"]

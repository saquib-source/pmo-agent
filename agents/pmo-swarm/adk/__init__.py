"""ISRDS PMO Swarm — ADK entry point. Exposes root_agent for `adk web .`"""
import os
from pathlib import Path

_ADK_DIR = Path(__file__).parent

# Resolve relative paths in .env to absolute before any imports use them
for _var in ["GOOGLE_APPLICATION_CREDENTIALS", "FIREBASE_SERVICE_ACCOUNT",
             "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH"]:
    _val = os.environ.get(_var)
    if _val and not os.path.isabs(_val):
        _resolved = str((_ADK_DIR / _val).resolve())
        if os.path.exists(_resolved):
            os.environ[_var] = _resolved
        else:
            print(f"⚠  {_var} → missing file: {_resolved} — using system default")
            os.environ.pop(_var, None)

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

# `root_agent` is imported lazily so that lightweight consumers (e.g. the
# Control UI importing adk.shared.*) don't pull in the full ADK orchestrator.
# `adk web .` and `from adk import root_agent` still work via __getattr__.
def __getattr__(name):
    if name == "root_agent":
        from .orchestrator import root_agent
        return root_agent
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = ["root_agent"]

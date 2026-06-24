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

from .orchestrator import root_agent

__all__ = ["root_agent"]

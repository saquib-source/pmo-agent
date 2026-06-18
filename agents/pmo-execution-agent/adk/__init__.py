"""ISRDS Agent — pmo-execution-agent. ADK entry point."""
import os
from pathlib import Path

# ── Resolve relative paths in .env to absolute (portable across machines) ──
_ADK_DIR = Path(__file__).parent

_PATH_ENV_VARS = [
    "GOOGLE_APPLICATION_CREDENTIALS",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "GRPC_DEFAULT_SSL_ROOTS_FILE_PATH",
]

for _var in _PATH_ENV_VARS:
    _val = os.environ.get(_var)
    if _val:
        _resolved = str((_ADK_DIR / _val).resolve()) if not os.path.isabs(_val) else _val
        if os.path.exists(_resolved):
            os.environ[_var] = _resolved
        else:
            # File missing (e.g., fresh clone without SSL certs) — unset so code uses system defaults
            del os.environ[_var]

# Trust the OS certificate store (fixes corporate proxy/firewall SSL errors)
import truststore
truststore.inject_into_ssl()

from .agent import root_agent

__all__ = ["root_agent"]


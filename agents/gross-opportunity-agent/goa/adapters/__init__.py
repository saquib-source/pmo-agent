from .base import Adapter
from .rest_adapter import RestAdapter
from .alert_email_adapter import AlertEmailAdapter
from .inbound_push_adapter import InboundPushAdapter
from .sam_gov import SamGovAdapter

_METHOD_MAP = {
    "rest": RestAdapter,
    "alert_email": AlertEmailAdapter,
    "inbound_push": InboundPushAdapter,
}

_SOURCE_MAP = {
    "sam_gov": SamGovAdapter,
}


def for_method(method: str) -> type[Adapter]:
    if method not in _METHOD_MAP:
        raise ValueError(f"Unknown adapter method: {method}. Register it in adapters/__init__.py")
    return _METHOD_MAP[method]


def for_source(source_id: str) -> type[Adapter]:
    return _SOURCE_MAP.get(source_id, RestAdapter)

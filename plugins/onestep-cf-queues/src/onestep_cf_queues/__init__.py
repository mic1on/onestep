from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import CFQueue, CFQueuesConnector, CFQueuesDelivery
from .resilience import (
    CFQueuesErrorCause,
    as_cf_connector_operation_error,
    classify_cf_error,
)
from .resources import register_resources

try:
    __version__ = _package_version("onestep-cf-queues")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

register = register_resources

__all__ = [
    "CFQueue",
    "CFQueuesConnector",
    "CFQueuesDelivery",
    "CFQueuesErrorCause",
    "__version__",
    "as_cf_connector_operation_error",
    "classify_cf_error",
    "register",
    "register_resources",
]

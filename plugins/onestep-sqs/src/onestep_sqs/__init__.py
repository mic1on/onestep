from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import SQSConnector, SQSDelivery, SQSQueue
from .resources import register_resources
from .resilience import classify_sqs_error, register_error_classifiers

register_error_classifiers()

try:
    __version__ = _package_version("onestep-sqs")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

register = register_resources

__all__ = [
    "SQSConnector",
    "SQSDelivery",
    "SQSQueue",
    "__version__",
    "classify_sqs_error",
    "register",
    "register_error_classifiers",
    "register_resources",
]

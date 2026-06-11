from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import RabbitMQConnector, RabbitMQDelivery, RabbitMQQueue
from .resources import register_resources
from .resilience import classify_rabbitmq_error

try:
    __version__ = _package_version("onestep-mq")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

register = register_resources

__all__ = [
    "RabbitMQConnector",
    "RabbitMQDelivery",
    "RabbitMQQueue",
    "__version__",
    "classify_rabbitmq_error",
    "register",
    "register_resources",
]

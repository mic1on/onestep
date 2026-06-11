from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import RedisConnector, RedisStreamDelivery, RedisStreamQueue
from .resources import register_resources
from .resilience import classify_redis_error

try:
    __version__ = _package_version("onestep-redis")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

register = register_resources

__all__ = [
    "RedisConnector",
    "RedisStreamDelivery",
    "RedisStreamQueue",
    "__version__",
    "classify_redis_error",
    "register",
    "register_resources",
]

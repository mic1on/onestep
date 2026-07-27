from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import (
    ClickHouseConnector,
    ClickHousePayloadError,
    ClickHouseTableSink,
)
from .resources import register_resources

try:
    __version__ = _package_version("onestep-clickhouse")
except PackageNotFoundError:
    __version__ = "dev"

register = register_resources

__all__ = [
    "ClickHouseConnector",
    "ClickHousePayloadError",
    "ClickHouseTableSink",
    "__version__",
    "register",
    "register_resources",
]

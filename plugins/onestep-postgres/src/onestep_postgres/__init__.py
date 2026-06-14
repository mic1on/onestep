from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import (
    IncrementalDelivery,
    PostgresConnector,
    PostgresIncrementalSource,
    PostgresTableQueueDelivery,
    PostgresTableQueueSource,
    PostgresTableSink,
)
from .resources import register_resources
from .resilience import classify_sqlalchemy_error
from .state_sqlalchemy import SQLAlchemyCursorStore, SQLAlchemyStateStore

try:
    __version__ = _package_version("onestep-postgres")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

register = register_resources

__all__ = [
    "IncrementalDelivery",
    "PostgresConnector",
    "PostgresIncrementalSource",
    "PostgresTableQueueDelivery",
    "PostgresTableQueueSource",
    "PostgresTableSink",
    "SQLAlchemyCursorStore",
    "SQLAlchemyStateStore",
    "__version__",
    "classify_sqlalchemy_error",
    "register",
    "register_resources",
]

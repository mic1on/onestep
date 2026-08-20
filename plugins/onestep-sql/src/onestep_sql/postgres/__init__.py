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
from .execution_backend import (
    ExecutionLease,
    HeartbeatResult,
    PostgresExecutionBackend,
    StaleExecutionLease,
)
from .execution_source import PostgresExecutionDelivery, PostgresExecutionSource
from .resources import register_resources
from .resilience import classify_sqlalchemy_error
from .state_sqlalchemy import SQLAlchemyCursorStore, SQLAlchemyStateStore

try:
    __version__ = _package_version("onestep-sql")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

register = register_resources

__all__ = [
    "IncrementalDelivery",
    "ExecutionLease",
    "HeartbeatResult",
    "PostgresConnector",
    "PostgresExecutionBackend",
    "PostgresExecutionDelivery",
    "PostgresExecutionSource",
    "PostgresIncrementalSource",
    "PostgresTableQueueDelivery",
    "PostgresTableQueueSource",
    "PostgresTableSink",
    "SQLAlchemyCursorStore",
    "SQLAlchemyStateStore",
    "StaleExecutionLease",
    "__version__",
    "classify_sqlalchemy_error",
    "register",
    "register_resources",
]

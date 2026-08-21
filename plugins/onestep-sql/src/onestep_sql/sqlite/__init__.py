from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

from .connector import (
    IncrementalDelivery,
    IncrementalTableSource,
    SQLiteConnector,
    TableQueueDelivery,
    TableQueueSource,
    TableSink,
)
from .resilience import classify_sqlalchemy_error
from .resources import register_resources
from .state_sqlalchemy import SQLAlchemyCursorStore, SQLAlchemyStateStore

try:
    __version__ = _package_version("onestep-sql")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

register = register_resources

__all__ = [
    "IncrementalDelivery",
    "IncrementalTableSource",
    "SQLAlchemyCursorStore",
    "SQLAlchemyStateStore",
    "SQLiteConnector",
    "TableQueueDelivery",
    "TableQueueSource",
    "TableSink",
    "__version__",
    "classify_sqlalchemy_error",
    "register",
    "register_resources",
]

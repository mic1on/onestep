from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import (
    IncrementalDelivery,
    IncrementalTableSource,
    MySQLConnector,
    TableQueueDelivery,
    TableQueueSource,
    TableSink,
)
from .resources import register_resources
from .resilience import classify_sqlalchemy_error, register_error_classifiers
from .state_sqlalchemy import SQLAlchemyCursorStore, SQLAlchemyStateStore

register_error_classifiers()

try:
    __version__ = _package_version("onestep-mysql")
except PackageNotFoundError:  # pragma: no cover - local source tree before install
    __version__ = "dev"

register = register_resources

__all__ = [
    "IncrementalDelivery",
    "IncrementalTableSource",
    "MySQLConnector",
    "SQLAlchemyCursorStore",
    "SQLAlchemyStateStore",
    "TableQueueDelivery",
    "TableQueueSource",
    "TableSink",
    "__version__",
    "classify_sqlalchemy_error",
    "register",
    "register_error_classifiers",
    "register_resources",
]

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _package_version

from .connector import (
    ElasticsearchBulkError,
    ElasticsearchBulkItemError,
    ElasticsearchBulkSink,
    ElasticsearchConnector,
)
from .resources import register_resources

try:
    __version__ = _package_version("onestep-elasticsearch")
except PackageNotFoundError:
    __version__ = "dev"

register = register_resources

__all__ = [
    "ElasticsearchBulkError",
    "ElasticsearchBulkItemError",
    "ElasticsearchBulkSink",
    "ElasticsearchConnector",
    "__version__",
    "register",
    "register_resources",
]

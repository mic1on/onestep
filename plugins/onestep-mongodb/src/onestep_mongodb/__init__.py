from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version as _package_version

from .connector import MongoDBChangeStreamDelivery, MongoDBChangeStreamSource, MongoDBCollectionSink, MongoDBConnector, MongoDBPayloadError, MongoDBPollingDelivery, MongoDBPollingSource
from .resources import register_resources

try:
    __version__ = _package_version("onestep-mongodb")
except PackageNotFoundError:
    __version__ = "dev"

register = register_resources
__all__ = ["MongoDBChangeStreamDelivery", "MongoDBChangeStreamSource", "MongoDBCollectionSink", "MongoDBConnector", "MongoDBPayloadError", "MongoDBPollingDelivery", "MongoDBPollingSource", "__version__", "register", "register_resources"]

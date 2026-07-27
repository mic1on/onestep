from __future__ import annotations

from importlib import metadata as importlib_metadata

from onestep_mongodb import (
    MongoDBChangeStreamDelivery,
    MongoDBChangeStreamSource,
    MongoDBCollectionSink,
    MongoDBConnector,
    MongoDBPayloadError,
    MongoDBPollingDelivery,
    MongoDBPollingSource,
    register,
    register_resources,
)


def test_public_surface_and_entry_point() -> None:
    assert register is register_resources
    assert MongoDBConnector.__name__ == "MongoDBConnector"
    assert MongoDBPollingSource.__name__ == "MongoDBPollingSource"
    assert MongoDBPollingDelivery.__name__ == "MongoDBPollingDelivery"
    assert MongoDBChangeStreamSource.__name__ == "MongoDBChangeStreamSource"
    assert MongoDBChangeStreamDelivery.__name__ == "MongoDBChangeStreamDelivery"
    assert MongoDBCollectionSink.__name__ == "MongoDBCollectionSink"
    assert MongoDBPayloadError.__name__ == "MongoDBPayloadError"
    entries = importlib_metadata.entry_points()
    selected = entries.select(group="onestep.resources") if hasattr(entries, "select") else entries.get("onestep.resources", ())
    assert any(item.name == "mongodb" and item.value == "onestep_mongodb:register" for item in selected)

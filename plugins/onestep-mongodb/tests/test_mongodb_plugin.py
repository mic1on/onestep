from __future__ import annotations

import traceback
from importlib import metadata as importlib_metadata

import pytest
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
from onestep_mongodb.resources import _build_polling
from pymongo.errors import AutoReconnect

from onestep import (
    ConnectorOperation,
    ConnectorOperationError,
    InMemoryCursorStore,
    ResourceBuildContext,
    ResourceRegistry,
    load_app_config,
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


def _config(resources):
    return {"apiVersion": "onestep/v1alpha1", "kind": "App", "app": {"name": "mongo"}, "resources": resources, "tasks": []}


def test_catalog_roles_and_topology_are_exact() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    assert catalog["mongodb"].roles == ("connector",)
    assert catalog["mongodb_polling"].topology_fields == ("collection", "cursor", "batch_size", "poll_interval_s")
    assert catalog["mongodb_change_stream"].topology_fields == ("collection", "full_document", "batch_size", "max_await_time_ms")
    assert catalog["mongodb_collection_sink"].topology_fields == ("collection", "mode", "keys", "batch_size")


def test_strict_yaml_builds_all_resource_roles() -> None:
    app = load_app_config(_config({
        "mongo": {"type": "mongodb", "uri": "mongodb://localhost/app?replicaSet=rs0", "database": "app"},
        "poll": {"type": "mongodb_polling", "connector": "mongo", "collection": "events", "cursor": ["updated_at", "_id"]},
        "changes": {"type": "mongodb_change_stream", "connector": "mongo", "collection": "events", "full_document": "updateLookup"},
        "sink": {"type": "mongodb_collection_sink", "connector": "mongo", "collection": "archive", "mode": "upsert", "keys": ["event_id"]},
    }), strict=True)
    assert isinstance(app.resources["mongo"], MongoDBConnector)
    assert isinstance(app.resources["poll"], MongoDBPollingSource)
    assert isinstance(app.resources["changes"], MongoDBChangeStreamSource)
    assert isinstance(app.resources["sink"], MongoDBCollectionSink)


def test_polling_builder_accepts_cursor_store_capability() -> None:
    connector = MongoDBConnector("mongodb://localhost", database="app", client=object())
    store = InMemoryCursorStore()
    values = {"mongo": connector, "state": store}
    ctx = ResourceBuildContext(name="poll", type="mongodb_polling", field="resources.poll", _resolve=values.__getitem__)
    source = _build_polling(ctx, {"type": "mongodb_polling", "connector": "mongo", "collection": "events", "state": "state"})
    assert source.state is store


@pytest.mark.parametrize("resource", [
    {"type": "mongodb", "uri": "mongodb://localhost/app?w=0", "database": "app"},
    {"type": "mongodb_polling", "connector": "mongo", "collection": "events", "cursor": ["updated_at", "updated_at"]},
    {"type": "mongodb_polling", "connector": "mongo", "collection": "events", "cursor": ["_id", "updated_at"]},
    {"type": "mongodb_change_stream", "connector": "mongo", "collection": "events", "pipeline": {}},
    {"type": "mongodb_change_stream", "connector": "mongo", "collection": "events", "full_document": "lookup"},
    {"type": "mongodb_collection_sink", "connector": "mongo", "collection": "events", "mode": "upsert"},
    {"type": "mongodb_collection_sink", "connector": "mongo", "collection": "events", "unknown": True},
])
def test_strict_yaml_rejects_invalid_resource(resource) -> None:
    resources = {"mongo": {"type": "mongodb", "uri": "mongodb://localhost", "database": "app"}, "target": resource}
    if resource["type"] == "mongodb":
        resources = {"mongo": resource}
    with pytest.raises((TypeError, ValueError)):
        load_app_config(_config(resources), strict=True)


def _assert_public_error_is_redacted(exc: ConnectorOperationError, secret: str) -> None:
    rendered = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    assert secret not in rendered
    assert secret not in str(exc.cause)
    assert exc.__suppress_context__ is True


def test_collection_normalizes_client_initialization_errors() -> None:
    secret = "mongo-super-secret"

    class Client:
        def __getitem__(self, name):
            raise AutoReconnect(f"cannot connect with {secret}")

    connector = MongoDBConnector(
        "mongodb://local",
        database="app",
        client_options={"password": secret},
        client=Client(),
    )
    with pytest.raises(ConnectorOperationError) as captured:
        connector.collection("events")

    assert captured.value.operation is ConnectorOperation.OPEN
    _assert_public_error_is_redacted(captured.value, secret)


@pytest.mark.asyncio
async def test_change_stream_open_normalizes_watch_errors() -> None:
    secret = "mongo-super-secret"

    class Collection:
        async def watch(self, pipeline, **options):
            raise AutoReconnect(f"cannot watch with {secret}")

    class Database:
        def __getitem__(self, name):
            return Collection()

    class Client:
        def __getitem__(self, name):
            return Database()

    connector = MongoDBConnector(
        "mongodb://local",
        database="app",
        client_options={"password": secret},
        client=Client(),
    )
    source = connector.watch_collection("events")
    with pytest.raises(ConnectorOperationError) as captured:
        await source.open()

    assert captured.value.operation is ConnectorOperation.OPEN
    _assert_public_error_is_redacted(captured.value, secret)


@pytest.mark.asyncio
async def test_change_stream_cleanup_cannot_replace_redacted_fetch_error() -> None:
    secret = "mongo-super-secret"

    class Stream:
        async def try_next(self):
            raise AutoReconnect(f"fetch failed with {secret}")

        async def close(self):
            raise AutoReconnect(f"cleanup failed with {secret}")

    class Collection:
        async def watch(self, pipeline, **options):
            return Stream()

    class Database:
        def __getitem__(self, name):
            return Collection()

    class Client:
        def __getitem__(self, name):
            return Database()

    connector = MongoDBConnector(
        "mongodb://local",
        database="app",
        client_options={"password": secret},
        client=Client(),
    )
    source = connector.watch_collection("events")
    with pytest.raises(ConnectorOperationError) as captured:
        await source.fetch(1)

    assert captured.value.operation is ConnectorOperation.FETCH
    _assert_public_error_is_redacted(captured.value, secret)

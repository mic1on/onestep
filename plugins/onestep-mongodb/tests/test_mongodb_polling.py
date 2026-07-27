from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bson import ObjectId

from onestep import InMemoryCursorStore
from onestep_mongodb import MongoDBConnector
from onestep_mongodb.connector import _ContiguousGenerationTracker
from onestep_mongodb.state_codec import decode_state, encode_state


def test_extended_json_round_trips_bson_values() -> None:
    value = [ObjectId("64b64c1234567890abcdef12"), datetime(2026, 7, 27, tzinfo=timezone.utc)]
    encoded = encode_state(value)
    assert isinstance(encoded, dict)
    assert decode_state(encoded) == value


@pytest.mark.asyncio
async def test_out_of_order_ack_does_not_cross_gap() -> None:
    saved: list[object] = []

    async def save(token):
        saved.append(token)

    tracker = _ContiguousGenerationTracker(save)
    first = tracker.add("one")
    second = tracker.add("two")
    await tracker.complete(second, advance=True)
    assert saved == []
    await tracker.complete(first, advance=True)
    assert saved == ["two"]


@pytest.mark.asyncio
async def test_invalidated_generation_ignores_late_ack_and_blocks_reopen() -> None:
    saved: list[object] = []
    tracker = _ContiguousGenerationTracker(lambda token: _append(saved, token))
    first = tracker.add("one")
    second = tracker.add("two")
    await tracker.invalidate(first.generation)
    assert tracker.can_fetch is False
    await tracker.complete(first, advance=True)
    assert saved == [] and tracker.can_fetch is False
    await tracker.complete(second, advance=False)
    assert tracker.can_fetch is True


async def _append(values, value):
    values.append(value)


def test_composite_keyset_query_is_lexicographic() -> None:
    source = MongoDBConnector("mongodb://local", database="app", client=object()).poll_collection("events", cursor=("updated_at", "_id"))
    query = source._cursor_query((10, ObjectId("64b64c1234567890abcdef12")))
    assert query == {"$or": [
        {"updated_at": {"$gt": 10}},
        {"updated_at": 10, "_id": {"$gt": ObjectId("64b64c1234567890abcdef12")}},
    ]}


def test_filter_combines_with_keyset_under_and() -> None:
    source = MongoDBConnector("mongodb://local", database="app", client=object()).poll_collection("events", cursor=("updated_at",), filter={"archived": False})
    query = source._query((10, ObjectId("64b64c1234567890abcdef12")))
    assert query == {"$and": [{"archived": False}, {"$or": [{"updated_at": {"$gt": 10}}, {"updated_at": 10, "_id": {"$gt": ObjectId("64b64c1234567890abcdef12")}}]}]}


def test_id_must_be_final_when_explicit() -> None:
    with pytest.raises(ValueError, match="final"):
        MongoDBConnector("mongodb://local", database="app", client=object()).poll_collection("events", cursor=("_id", "updated_at"))


class RecordingStore:
    def __init__(self, loaded=None) -> None:
        self.loaded = loaded
        self.saved: list[tuple[str, object]] = []

    async def load(self, key):
        return self.loaded

    async def save(self, key, value):
        self.saved.append((key, value))
        self.loaded = value


class FakeCursor:
    def __init__(self, documents) -> None:
        self.documents = list(documents)
        self.sort_value = None
        self.limit_value = None

    def sort(self, value):
        self.sort_value = value
        return self

    def limit(self, value):
        self.limit_value = value
        self.documents = self.documents[:value]
        return self

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration:
            raise StopAsyncIteration


class FakeCollection:
    def __init__(self, documents) -> None:
        self.documents = list(documents)
        self.find_calls: list[tuple[dict, object, FakeCursor]] = []

    def find(self, query, projection):
        cursor = FakeCursor(self.documents)
        self.find_calls.append((query, projection, cursor))
        return cursor


class FakeDatabase:
    def __init__(self, collection) -> None:
        self.collection = collection

    def __getitem__(self, name):
        return self.collection


class FakeClient:
    def __init__(self, collection) -> None:
        self.database = FakeDatabase(collection)

    def __getitem__(self, name):
        return self.database


@pytest.mark.asyncio
async def test_polling_persists_only_the_contiguous_ack_prefix() -> None:
    documents = [
        {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1},
        {"_id": ObjectId("64b64c1234567890abcdef13"), "updated_at": 2},
    ]
    store = RecordingStore()
    collection = FakeCollection(documents)
    connector = MongoDBConnector("mongodb://local", database="app", client=FakeClient(collection))
    source = connector.poll_collection("events", cursor=("updated_at", "_id"), state=store)

    first, second = await source.fetch(2)
    await second.ack()
    assert store.saved == []
    await first.ack()
    assert decode_state(store.saved[-1][1]) == [2, documents[1]["_id"]]
    assert collection.find_calls[0][2].sort_value == [("updated_at", 1), ("_id", 1)]
    assert collection.find_calls[0][2].limit_value == 2


@pytest.mark.asyncio
async def test_retry_waits_for_stale_generation_then_replays_committed_state() -> None:
    documents = [
        {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1},
        {"_id": ObjectId("64b64c1234567890abcdef13"), "updated_at": 2},
    ]
    store = RecordingStore()
    collection = FakeCollection(documents)
    source = MongoDBConnector("mongodb://local", database="app", client=FakeClient(collection)).poll_collection(
        "events", cursor=("updated_at", "_id"), state=store
    )

    first, second = await source.fetch(2)
    await first.retry()
    assert await source.fetch(2) == []
    await second.fail(RuntimeError("terminal stale handler"))
    await source.fetch(2)

    assert store.saved == []
    assert collection.find_calls[-1][0] == {}


@pytest.mark.asyncio
async def test_terminal_fail_advances_polling_cursor() -> None:
    document = {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1}
    store = RecordingStore()
    source = MongoDBConnector("mongodb://local", database="app", client=FakeClient(FakeCollection([document]))).poll_collection(
        "events", cursor=("updated_at", "_id"), state=store
    )
    delivery = (await source.fetch(1))[0]
    await delivery.fail(RuntimeError("terminal handler failure"))
    assert decode_state(store.saved[-1][1]) == [1, document["_id"]]


@pytest.mark.asyncio
async def test_owned_client_is_lazy_and_closes_once(monkeypatch) -> None:
    import pymongo

    collection = FakeCollection([])

    class OwnedClient(FakeClient):
        def __init__(self):
            super().__init__(collection)
            self.close_calls = 0

        async def close(self):
            self.close_calls += 1

    client = OwnedClient()
    factory_calls = []

    def build_client(uri, **options):
        factory_calls.append((uri, options))
        return client

    monkeypatch.setattr(pymongo, "AsyncMongoClient", build_client)
    connector = MongoDBConnector("mongodb://local", database="app")
    assert factory_calls == []
    assert connector.collection("events") is collection
    assert factory_calls == [("mongodb://local", {})]
    await connector.close()
    await connector.close()
    assert client.close_calls == 1

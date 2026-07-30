from __future__ import annotations

import asyncio
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
async def test_later_ack_then_earlier_retry_does_not_cross_generation_gap() -> None:
    saved: list[object] = []
    tracker = _ContiguousGenerationTracker(lambda token: _append(saved, token))
    first = tracker.add("one")
    second = tracker.add("two")

    await tracker.complete(second, advance=True)
    await tracker.invalidate(first.generation)
    await tracker.complete(first, advance=False)

    assert saved == []
    assert tracker.can_fetch is True


@pytest.mark.asyncio
async def test_save_failure_keeps_contiguous_prefix_releasable() -> None:
    save_calls = 0

    async def save(token):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise RuntimeError("state unavailable")

    tracker = _ContiguousGenerationTracker(save)
    first = tracker.add("one")
    second = tracker.add("two")
    await tracker.complete(second, advance=True)

    with pytest.raises(RuntimeError, match="state unavailable"):
        await tracker.complete(first, advance=True)

    assert list(tracker._pending) == [first, second]
    assert tracker._outstanding == {first.generation: 1}

    await tracker.invalidate(first.generation)
    await tracker.complete(first, advance=False)

    assert list(tracker._pending) == []
    assert tracker.can_fetch is True


@pytest.mark.asyncio
async def test_ack_can_be_reinvoked_after_transient_save_failure() -> None:
    saved: list[object] = []
    save_calls = 0

    async def save(token):
        nonlocal save_calls
        save_calls += 1
        if save_calls == 1:
            raise RuntimeError("state unavailable")
        saved.append(token)

    tracker = _ContiguousGenerationTracker(save)
    tracked = tracker.add("one")

    with pytest.raises(RuntimeError, match="state unavailable"):
        await tracker.complete(tracked, advance=True)

    assert tracked.completed is False
    await tracker.complete(tracked, advance=True)

    assert saved == ["one"]
    assert list(tracker._pending) == []


@pytest.mark.asyncio
async def test_save_cancellation_rolls_back_tracker_state() -> None:
    async def save(token):
        raise asyncio.CancelledError

    tracker = _ContiguousGenerationTracker(save)
    tracked = tracker.add("one")

    with pytest.raises(asyncio.CancelledError):
        await tracker.complete(tracked, advance=True)

    assert tracked.completed is False
    assert list(tracker._pending) == [tracked]
    assert tracker._outstanding == {tracked.generation: 1}

    await tracker.invalidate(tracked.generation)
    await tracker.complete(tracked, advance=False)
    assert tracker.can_fetch is True


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


@pytest.mark.parametrize("batch_size", [0, -1, True, 1.5])
def test_polling_rejects_invalid_batch_size(batch_size) -> None:
    with pytest.raises((TypeError, ValueError), match="batch_size"):
        MongoDBConnector(
            "mongodb://local", database="app", client=object()
        ).poll_collection("events", batch_size=batch_size)


@pytest.mark.parametrize("poll_interval_s", [-0.1, True])
def test_polling_rejects_invalid_poll_interval(poll_interval_s) -> None:
    with pytest.raises((TypeError, ValueError), match="poll_interval_s"):
        MongoDBConnector(
            "mongodb://local", database="app", client=object()
        ).poll_collection("events", poll_interval_s=poll_interval_s)


def test_zero_poll_interval_is_accepted() -> None:
    connector = MongoDBConnector("mongodb://local", database="app", client=object())

    assert connector.poll_collection("events", poll_interval_s=0).poll_interval_s == 0
    assert connector.watch_collection("events", poll_interval_s=0).poll_interval_s == 0


@pytest.mark.parametrize(
    ("projection", "cursor"),
    [
        ({"updated_at": 0}, ("updated_at", "_id")),
        ({"value": 1}, ("updated_at", "_id")),
        ({"_id": 1}, ("updated_at", "_id")),
        ({"_id": 0}, ("updated_at", "_id")),
        ({"updated_at": {"$literal": 1}}, ("updated_at", "_id")),
        ({"_id": {"$literal": "fixed"}}, ("_id",)),
    ],
)
def test_polling_projection_must_preserve_effective_cursor(
    projection, cursor
) -> None:
    with pytest.raises(ValueError, match="projection.*cursor"):
        MongoDBConnector(
            "mongodb://local", database="app", client=object()
        ).poll_collection("events", projection=projection, cursor=cursor)


@pytest.mark.parametrize(
    ("projection", "cursor"),
    [
        ({"value": 0}, ("updated_at", "_id")),
        ({"updated_at": 1}, ("updated_at", "_id")),
        ({"updated_at": True}, ("updated_at", "_id")),
        ({"value": 1}, ("_id",)),
    ],
)
def test_polling_projection_accepts_cursor_preserving_shapes(
    projection, cursor
) -> None:
    source = MongoDBConnector(
        "mongodb://local", database="app", client=object()
    ).poll_collection("events", projection=projection, cursor=cursor)

    assert source.projection == projection


class RecordingStore:
    def __init__(self, loaded=None) -> None:
        self.loaded = loaded
        self.saved: list[tuple[str, object]] = []

    async def load(self, key):
        return self.loaded

    async def save(self, key, value):
        self.saved.append((key, value))
        self.loaded = value


class FailOnceStore(RecordingStore):
    def __init__(self, loaded=None) -> None:
        super().__init__(loaded)
        self.save_calls = 0

    async def save(self, key, value):
        self.save_calls += 1
        if self.save_calls == 1:
            raise RuntimeError("state unavailable")
        await super().save(key, value)


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
async def test_later_ack_then_earlier_retry_replays_from_committed_state() -> None:
    documents = [
        {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1},
        {"_id": ObjectId("64b64c1234567890abcdef13"), "updated_at": 2},
    ]
    store = RecordingStore()
    collection = FakeCollection(documents)
    source = MongoDBConnector(
        "mongodb://local", database="app", client=FakeClient(collection)
    ).poll_collection("events", cursor=("updated_at", "_id"), state=store)
    first, second = await source.fetch(2)

    await second.ack()
    await first.retry()
    replayed = await source.fetch(2)

    assert store.saved == []
    assert collection.find_calls[-1][0] == {}
    assert [item.payload["updated_at"] for item in replayed] == [1, 2]


@pytest.mark.asyncio
async def test_polling_state_save_failure_does_not_advance_or_drop_prefix() -> None:
    documents = [
        {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1},
        {"_id": ObjectId("64b64c1234567890abcdef13"), "updated_at": 2},
    ]
    store = FailOnceStore()
    collection = FakeCollection(documents)
    source = MongoDBConnector(
        "mongodb://local", database="app", client=FakeClient(collection)
    ).poll_collection("events", cursor=("updated_at", "_id"), state=store)
    first, second = await source.fetch(2)

    await second.ack()
    with pytest.raises(RuntimeError, match="state unavailable"):
        await first.ack()

    assert source._committed is None
    assert store.saved == []

    await first.retry()
    replayed = await source.fetch(2)
    assert collection.find_calls[-1][0] == {}
    assert [item.payload["updated_at"] for item in replayed] == [1, 2]


@pytest.mark.asyncio
async def test_polling_fail_remains_reinvocable_after_state_save_failure() -> None:
    document = {"_id": ObjectId("64b64c1234567890abcdef12"), "updated_at": 1}
    store = FailOnceStore()
    source = MongoDBConnector(
        "mongodb://local",
        database="app",
        client=FakeClient(FakeCollection([document])),
    ).poll_collection("events", cursor=("updated_at", "_id"), state=store)
    delivery = (await source.fetch(1))[0]

    with pytest.raises(RuntimeError, match="state unavailable"):
        await delivery.fail(RuntimeError("terminal handler failure"))

    assert delivery._terminal is False
    await delivery.fail(RuntimeError("terminal handler failure"))

    assert delivery._terminal is True
    assert decode_state(store.saved[-1][1]) == [1, document["_id"]]


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

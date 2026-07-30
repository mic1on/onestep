from __future__ import annotations

import asyncio

import pytest
from bson import ObjectId
from pymongo.errors import OperationFailure

from onestep import ConnectorErrorKind, ConnectorOperationError
from onestep_mongodb import MongoDBConnector
from onestep_mongodb.state_codec import decode_state, encode_state


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


class FakeChangeStream:
    def __init__(self, events) -> None:
        self.events = list(events)
        self.closed = False

    async def try_next(self):
        return self.events.pop(0) if self.events else None

    async def close(self):
        self.closed = True


class FailingCloseStream(FakeChangeStream):
    def __init__(self, events, error_type) -> None:
        super().__init__(events)
        self.error_type = error_type
        self.close_calls = 0

    async def close(self):
        self.close_calls += 1
        raise self.error_type("close unavailable")


class FakeWatchCollection:
    def __init__(self, streams) -> None:
        self.streams = list(streams)
        self.watch_calls: list[dict] = []

    async def watch(self, pipeline, **options):
        self.watch_calls.append({"pipeline": pipeline, **options})
        return self.streams.pop(0)


class FakeWatchDatabase:
    def __init__(self, collection) -> None:
        self.collection = collection

    def __getitem__(self, name):
        return self.collection


class FakeWatchClient:
    def __init__(self, collection) -> None:
        self.database = FakeWatchDatabase(collection)

    def __getitem__(self, name):
        return self.database


def _event(hex_id: str, operation: str):
    object_id = ObjectId(hex_id)
    return {"_id": {"token": hex_id}, "operationType": operation, "documentKey": {"_id": object_id}}


@pytest.mark.parametrize(
    ("option", "value"),
    [
        ("batch_size", 0),
        ("batch_size", True),
        ("max_await_time_ms", 0),
        ("max_await_time_ms", 1.5),
        ("poll_interval_s", -0.1),
        ("poll_interval_s", True),
    ],
)
def test_change_stream_rejects_invalid_numeric_options(option, value) -> None:
    with pytest.raises((TypeError, ValueError), match=option):
        MongoDBConnector(
            "mongodb://local", database="app", client=object()
        ).watch_collection("events", **{option: value})


@pytest.mark.asyncio
async def test_default_watch_emits_complete_raw_delete_event() -> None:
    raw_event = _event("64b64c1234567890abcdef12", "delete")
    stream = FakeChangeStream([raw_event])
    collection = FakeWatchCollection([stream])
    connector = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection))
    source = connector.watch_collection("events")

    delivery = (await source.fetch(1))[0]

    assert collection.watch_calls[0]["full_document"] == "updateLookup"
    assert "resume_after" not in collection.watch_calls[0]
    assert delivery.envelope.body == raw_event
    assert delivery.envelope.body["operationType"] == "delete"
    assert delivery.envelope.body["documentKey"] == {"_id": raw_event["documentKey"]["_id"]}


@pytest.mark.asyncio
async def test_restart_passes_decoded_resume_token() -> None:
    token = {"token": "persisted"}
    store = RecordingStore(encode_state(token))
    collection = FakeWatchCollection([FakeChangeStream([])])
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection("events", state=store)

    await source.fetch(1)

    assert collection.watch_calls[0]["resume_after"] == token


@pytest.mark.asyncio
async def test_change_tokens_persist_only_after_contiguous_ack() -> None:
    first_event = _event("64b64c1234567890abcdef12", "insert")
    second_event = _event("64b64c1234567890abcdef13", "update")
    store = RecordingStore()
    collection = FakeWatchCollection([FakeChangeStream([first_event, second_event])])
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection("events", state=store)
    first, second = await source.fetch(2)

    await second.ack()
    assert store.saved == []
    await first.ack()

    assert decode_state(store.saved[-1][1]) == second_event["_id"]


@pytest.mark.asyncio
async def test_change_stream_state_save_failure_reopens_without_lost_token() -> None:
    first_event = _event("64b64c1234567890abcdef12", "insert")
    second_event = _event("64b64c1234567890abcdef13", "update")
    first_stream = FakeChangeStream([first_event, second_event])
    replacement = FakeChangeStream([])
    collection = FakeWatchCollection([first_stream, replacement])
    store = FailOnceStore()
    source = MongoDBConnector(
        "mongodb://local", database="app", client=FakeWatchClient(collection)
    ).watch_collection("events", state=store)
    first, second = await source.fetch(2)

    await second.ack()
    with pytest.raises(RuntimeError, match="state unavailable"):
        await first.ack()

    assert source._resume_token is None
    assert store.saved == []

    await first.retry()
    await source.fetch(2)
    assert len(collection.watch_calls) == 2
    assert "resume_after" not in collection.watch_calls[1]


@pytest.mark.asyncio
async def test_change_stream_fail_can_be_reinvoked_after_state_save_failure() -> None:
    event = _event("64b64c1234567890abcdef12", "insert")
    store = FailOnceStore()
    collection = FakeWatchCollection([FakeChangeStream([event])])
    source = MongoDBConnector(
        "mongodb://local", database="app", client=FakeWatchClient(collection)
    ).watch_collection("events", state=store)
    delivery = (await source.fetch(1))[0]

    with pytest.raises(RuntimeError, match="state unavailable"):
        await delivery.fail(RuntimeError("terminal handler failure"))

    assert delivery._terminal is False
    await delivery.fail(RuntimeError("terminal handler failure"))

    assert delivery._terminal is True
    assert decode_state(store.saved[-1][1]) == event["_id"]
    assert source._tracker.can_fetch is True


@pytest.mark.asyncio
async def test_retry_closes_stream_and_waits_for_stale_delivery() -> None:
    first_stream = FakeChangeStream([_event("64b64c1234567890abcdef12", "insert"), _event("64b64c1234567890abcdef13", "update")])
    replacement = FakeChangeStream([])
    collection = FakeWatchCollection([first_stream, replacement])
    store = RecordingStore()
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection("events", state=store)
    first, second = await source.fetch(2)

    await first.retry()
    assert first_stream.closed is True
    assert await source.fetch(2) == []
    assert len(collection.watch_calls) == 1
    await second.release_unstarted()
    await source.fetch(2)

    assert store.saved == []
    assert len(collection.watch_calls) == 2
    assert "resume_after" not in collection.watch_calls[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("callback", ["retry", "release_unstarted"])
@pytest.mark.parametrize("error_type", [RuntimeError, asyncio.CancelledError])
async def test_invalidation_settles_delivery_when_stream_close_fails(
    callback, error_type
) -> None:
    stream = FailingCloseStream(
        [_event("64b64c1234567890abcdef12", "insert")], error_type
    )
    collection = FakeWatchCollection([stream])
    source = MongoDBConnector(
        "mongodb://local", database="app", client=FakeWatchClient(collection)
    ).watch_collection("events", state=RecordingStore())
    delivery = (await source.fetch(1))[0]

    await getattr(delivery, callback)()

    assert delivery._terminal is True
    assert source._stream is None
    assert source._tracker.can_fetch is True
    assert stream.close_calls == 1


@pytest.mark.asyncio
async def test_history_lost_is_permanent_and_never_falls_back_to_now() -> None:
    class HistoryLostStream(FakeChangeStream):
        async def try_next(self):
            raise OperationFailure("resume history lost", code=286)

    collection = FakeWatchCollection([HistoryLostStream([])])
    store = RecordingStore(encode_state({"token": "old"}))
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection("events", state=store)

    with pytest.raises(ConnectorOperationError) as captured:
        await source.fetch(1)

    assert captured.value.kind is ConnectorErrorKind.PERMANENT
    assert "reset durable resume state" in str(captured.value)
    assert len(collection.watch_calls) == 1
    assert collection.watch_calls[0]["resume_after"] == {"token": "old"}


@pytest.mark.asyncio
async def test_resumable_stream_failure_reopens_from_committed_token() -> None:
    class ResumableStream(FakeChangeStream):
        async def try_next(self):
            raise OperationFailure(
                "temporary stream failure",
                details={"errorLabels": ["ResumableChangeStreamError"]},
            )

    token = {"token": "persisted"}
    collection = FakeWatchCollection([ResumableStream([]), FakeChangeStream([])])
    source = MongoDBConnector("mongodb://local", database="app", client=FakeWatchClient(collection)).watch_collection(
        "events", state=RecordingStore(encode_state(token))
    )

    with pytest.raises(ConnectorOperationError) as captured:
        await source.fetch(1)
    assert captured.value.kind is ConnectorErrorKind.TRANSIENT

    assert await source.fetch(1) == []
    assert len(collection.watch_calls) == 2
    assert collection.watch_calls[1]["resume_after"] == token

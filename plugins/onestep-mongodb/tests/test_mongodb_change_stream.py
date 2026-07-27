from __future__ import annotations

import pytest
from bson import ObjectId

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


class FakeChangeStream:
    def __init__(self, events) -> None:
        self.events = list(events)
        self.closed = False

    async def try_next(self):
        return self.events.pop(0) if self.events else None

    async def close(self):
        self.closed = True


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


from pymongo.errors import OperationFailure
from onestep import ConnectorErrorKind, ConnectorOperationError


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

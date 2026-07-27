from __future__ import annotations

import pytest
from pymongo.errors import BulkWriteError

from onestep import ConnectorErrorKind, ConnectorOperationError, Envelope
from onestep_mongodb import MongoDBConnector, MongoDBPayloadError


class FakeWriteConcern:
    def __init__(self, acknowledged=True) -> None:
        self.acknowledged = acknowledged


class FakeSinkCollection:
    def __init__(self, *, acknowledged=True) -> None:
        self.write_concern = FakeWriteConcern(acknowledged)
        self.insert_one_calls = []
        self.insert_many_calls = []
        self.bulk_calls = []

    async def insert_one(self, document):
        self.insert_one_calls.append(document)
        return object()

    async def insert_many(self, documents, *, ordered):
        self.insert_many_calls.append((documents, ordered))
        return object()

    async def bulk_write(self, operations, *, ordered):
        self.bulk_calls.append((operations, ordered))
        return object()


class FakeSinkDatabase:
    def __init__(self, collection) -> None:
        self.collection = collection

    def __getitem__(self, name):
        return self.collection


class FakeSinkClient:
    def __init__(self, collection) -> None:
        self.database = FakeSinkDatabase(collection)

    def __getitem__(self, name):
        return self.database


def _connector(collection):
    return MongoDBConnector("mongodb://local", database="app", client=FakeSinkClient(collection))


@pytest.mark.asyncio
async def test_insert_mapping_and_chunked_sequence() -> None:
    collection = FakeSinkCollection()
    sink = _connector(collection).collection_sink("events", batch_size=2, ordered=True)
    await sink.send(Envelope(body={"_id": "one"}))
    await sink.send(Envelope(body=[{"_id": "two"}, {"_id": "three"}, {"_id": "four"}]))
    assert collection.insert_one_calls == [{"_id": "one"}]
    assert collection.insert_many_calls == [
        ([{"_id": "two"}, {"_id": "three"}], True),
        ([{"_id": "four"}], True),
    ]


@pytest.mark.asyncio
async def test_upsert_uses_all_keys_and_excludes_keys_and_id_from_set() -> None:
    collection = FakeSinkCollection()
    sink = _connector(collection).collection_sink("events", mode="upsert", keys=("tenant", "event_id"), ordered=False)
    await sink.send(Envelope(body={"_id": "mongo-id", "tenant": "a", "event_id": "1", "value": 3}))
    request = collection.bulk_calls[0][0][0]
    assert request._filter == {"tenant": "a", "event_id": "1"}
    assert request._doc == {"$set": {"value": 3}}
    assert collection.bulk_calls[0][1] is False


@pytest.mark.parametrize("body", [[], "text", [1], [{"event_id": "one"}]])
@pytest.mark.asyncio
async def test_invalid_upsert_payloads_fail_before_client_call(body) -> None:
    collection = FakeSinkCollection()
    sink = _connector(collection).collection_sink("events", mode="upsert", keys=("tenant", "event_id"))
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=body))
    assert captured.value.kind is ConnectorErrorKind.PERMANENT
    assert isinstance(captured.value.cause, MongoDBPayloadError)
    assert collection.bulk_calls == []


@pytest.mark.asyncio
async def test_unacknowledged_write_concern_is_rejected() -> None:
    collection = FakeSinkCollection(acknowledged=False)
    sink = _connector(collection).collection_sink("events")
    with pytest.raises(ValueError, match="acknowledged"):
        await sink.send(Envelope(body={"_id": "one"}))


@pytest.mark.asyncio
async def test_partial_insert_bulk_error_is_uncertain_and_redacted() -> None:
    class PartialCollection(FakeSinkCollection):
        async def insert_many(self, documents, *, ordered):
            raise BulkWriteError({"writeErrors": [{"index": 1, "code": 11000, "errmsg": "duplicate"}], "nInserted": 1})

    sink = _connector(PartialCollection()).collection_sink("events")
    with pytest.raises(ConnectorOperationError) as captured:
        await sink.send(Envelope(body=[{"_id": "one"}, {"_id": "one"}]))
    assert captured.value.kind is ConnectorErrorKind.UNCERTAIN
    assert captured.value.cause.failed_indexes == (1,)
    assert captured.value.cause.codes == (11000,)
    assert captured.value.cause.committed_count == 1
    assert "documents" not in str(captured.value)

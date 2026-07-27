from __future__ import annotations

import asyncio

import pytest
from bson import ObjectId

from onestep import Delivery, Envelope, OneStepApp, Source
from onestep_mongodb import MongoDBConnector


class Store:
    def __init__(self) -> None:
        self.saved = []

    async def load(self, key):
        return None

    async def save(self, key, value):
        self.saved.append((key, value))


class BlockingCursor:
    def __init__(self, document, started, release) -> None:
        self.document = document
        self.started = started
        self.release = release
        self.yielded = False

    def sort(self, value):
        return self

    def limit(self, value):
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self.yielded:
            raise StopAsyncIteration
        self.started.set()
        await self.release.wait()
        self.yielded = True
        return self.document


class BlockingStream:
    def __init__(self, event, started, release) -> None:
        self.event = event
        self.started = started
        self.release = release
        self.returned = False
        self.closed = False

    async def try_next(self):
        self.started.set()
        await self.release.wait()
        if self.returned:
            return None
        self.returned = True
        return self.event

    async def close(self):
        self.closed = True


class RuntimeCollection:
    def __init__(self, *, document=None, event=None, started, release) -> None:
        self.document = document
        self.event = event
        self.started = started
        self.release = release

    def find(self, query, projection):
        return BlockingCursor(self.document, self.started, self.release)

    async def watch(self, pipeline, **options):
        return BlockingStream(self.event, self.started, self.release)


class Database:
    def __init__(self, collection) -> None:
        self.collection = collection

    def __getitem__(self, name):
        return self.collection


class Client:
    def __init__(self, collection) -> None:
        self.database = Database(collection)

    def __getitem__(self, name):
        return self.database


async def _request_stop(app, action):
    if action == "shutdown":
        app.request_shutdown()
        return
    if action == "drain":
        app.request_drain()
        await app.wait_for_drain()
        app.request_shutdown()
        return
    app.request_task_pause("consume")
    await app.wait_for_task_pause("consume")
    app.request_shutdown()


@pytest.mark.parametrize("source_kind", ["polling", "change_stream"])
@pytest.mark.parametrize("action", ["shutdown", "drain", "pause"])
@pytest.mark.asyncio
async def test_stop_controls_release_unstarted_without_committing(source_kind, action) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    store = Store()
    object_id = ObjectId("64b64c1234567890abcdef12")
    collection = RuntimeCollection(
        document={"_id": object_id, "updated_at": 1},
        event={"_id": {"token": "one"}, "operationType": "insert", "documentKey": {"_id": object_id}},
        started=started,
        release=release,
    )
    connector = MongoDBConnector("mongodb://local", database="app", client=Client(collection))
    source = connector.poll_collection("events", cursor=("updated_at", "_id"), state=store) if source_kind == "polling" else connector.watch_collection("events", state=store)
    assert source.fetch_is_cancel_safe is False
    handled = []
    app = OneStepApp(f"mongo-{source_kind}-{action}", shutdown_timeout_s=1.0)

    @app.task(source=source, name="consume", concurrency=1)
    async def consume(ctx, item):
        handled.append(item)

    serving = asyncio.create_task(app.serve())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    stopping = asyncio.create_task(_request_stop(app, action))
    await asyncio.sleep(0)
    release.set()
    await asyncio.wait_for(stopping, timeout=2.0)
    await asyncio.wait_for(serving, timeout=2.0)

    assert handled == []
    assert store.saved == []
    assert source._tracker.can_fetch is True


class AckRecordingDelivery(Delivery):
    def __init__(self, envelope: Envelope) -> None:
        super().__init__(envelope)
        self.acked = False

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s=None) -> None:
        raise AssertionError("must not retry")

    async def fail(self, exc=None) -> None:
        raise AssertionError(f"unexpected failure: {exc}")


class OneShotSource(Source):
    poll_interval_s = 0.01

    def __init__(self, delivery) -> None:
        super().__init__("one-shot")
        self.delivery = delivery
        self.sent = False

    async def fetch(self, limit):
        if self.sent:
            return []
        self.sent = True
        return [self.delivery]


class BlockingSinkCollection:
    class WriteConcern:
        acknowledged = True

    write_concern = WriteConcern()

    def __init__(self, entered, release) -> None:
        self.entered = entered
        self.release = release

    async def insert_one(self, document):
        self.entered.set()
        await self.release.wait()
        return object()


@pytest.mark.asyncio
async def test_runtime_ack_follows_mongodb_write_acknowledgement() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    collection = BlockingSinkCollection(entered, release)
    connector = MongoDBConnector("mongodb://local", database="app", client=Client(collection))
    sink = connector.collection_sink("events")
    delivery = AckRecordingDelivery(Envelope(body={"_id": "one"}))
    app = OneStepApp("mongodb-sink-runtime-order", shutdown_timeout_s=1.0)

    @app.task(source=OneShotSource(delivery), emit=sink, concurrency=1)
    async def forward(ctx, item):
        ctx.app.request_shutdown()
        return item

    serving = asyncio.create_task(app.serve())
    await entered.wait()
    assert delivery.acked is False
    release.set()
    await asyncio.wait_for(serving, timeout=2.0)
    assert delivery.acked is True

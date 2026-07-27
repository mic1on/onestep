from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from pymongo import AsyncMongoClient

from onestep import Envelope
from onestep_mongodb import MongoDBConnector

URI = os.getenv("ONESTEP_MONGODB_URI")
pytestmark = [pytest.mark.integration, pytest.mark.skipif(not URI, reason="ONESTEP_MONGODB_URI is not configured")]


class DurableTestStore:
    def __init__(self) -> None:
        self.values = {}

    async def load(self, key):
        return self.values.get(key)

    async def save(self, key, value):
        self.values[key] = value


async def _next_delivery(source, *, attempts=20):
    for _ in range(attempts):
        deliveries = await source.fetch(10)
        if deliveries:
            return deliveries[0]
        await asyncio.sleep(0.05)
    raise AssertionError("change stream produced no delivery")


@pytest.mark.asyncio
async def test_polling_restart_reads_only_later_documents() -> None:
    name = f"poll_{uuid.uuid4().hex}"
    client = AsyncMongoClient(URI)
    database = client.get_default_database()
    collection = database[name]
    store = DurableTestStore()
    await collection.insert_many([{"event_id": "one", "updated_at": 1}, {"event_id": "two", "updated_at": 2}])
    connector = MongoDBConnector(URI, database=database.name, client=client)
    first_source = connector.poll_collection(name, cursor=("updated_at", "_id"), state=store, state_key=name)
    try:
        deliveries = await first_source.fetch(10)
        for delivery in deliveries:
            await delivery.ack()
        await collection.insert_one({"event_id": "three", "updated_at": 3})
        second_source = connector.poll_collection(name, cursor=("updated_at", "_id"), state=store, state_key=name)
        restarted = await second_source.fetch(10)
        assert [item.payload["event_id"] for item in restarted] == ["three"]
    finally:
        await collection.drop()
        await client.close()


@pytest.mark.asyncio
async def test_insert_and_stable_key_upsert_are_acknowledged() -> None:
    name = f"sink_{uuid.uuid4().hex}"
    client = AsyncMongoClient(URI)
    database = client.get_default_database()
    collection = database[name]
    connector = MongoDBConnector(URI, database=database.name, client=client)
    try:
        await connector.collection_sink(name).send(Envelope(body={"_id": "stable", "value": 1}))
        upsert = connector.collection_sink(name, mode="upsert", keys=("event_id",))
        await upsert.send(Envelope(body={"event_id": "evt", "value": 2}))
        await upsert.send(Envelope(body={"event_id": "evt", "value": 3}))
        assert (await collection.find_one({"_id": "stable"}))["value"] == 1
        assert (await collection.find_one({"event_id": "evt"}))["value"] == 3
    finally:
        await collection.drop()
        await client.close()


@pytest.mark.asyncio
async def test_raw_change_events_include_update_lookup_and_delete_key() -> None:
    name = f"changes_{uuid.uuid4().hex}"
    client = AsyncMongoClient(URI)
    database = client.get_default_database()
    collection = database[name]
    connector = MongoDBConnector(URI, database=database.name, client=client)
    source = connector.watch_collection(name)
    await source.open()
    try:
        inserted = await collection.insert_one({"value": 1})
        insert_event = await _next_delivery(source)
        await insert_event.ack()
        await collection.update_one({"_id": inserted.inserted_id}, {"$set": {"value": 2}})
        update_event = await _next_delivery(source)
        await update_event.ack()
        await collection.delete_one({"_id": inserted.inserted_id})
        delete_event = await _next_delivery(source)
        await delete_event.ack()
        assert insert_event.payload["operationType"] == "insert" and "_id" in insert_event.payload
        assert update_event.payload["operationType"] == "update" and update_event.payload["fullDocument"]["value"] == 2
        assert delete_event.payload["operationType"] == "delete"
        assert delete_event.payload["documentKey"] == {"_id": inserted.inserted_id}
    finally:
        await source.close()
        await collection.drop()
        await client.close()


@pytest.mark.asyncio
async def test_change_stream_resumes_after_acknowledged_token() -> None:
    name = f"resume_{uuid.uuid4().hex}"
    state_key = f"state-{name}"
    store = DurableTestStore()
    client = AsyncMongoClient(URI)
    database = client.get_default_database()
    collection = database[name]
    connector = MongoDBConnector(URI, database=database.name, client=client)
    first_source = connector.watch_collection(name, state=store, state_key=state_key)
    await first_source.open()
    try:
        await collection.insert_one({"sequence": 1})
        first = await _next_delivery(first_source)
        await first.ack()
        await first_source.close()
        second_source = connector.watch_collection(name, state=store, state_key=state_key)
        await second_source.open()
        await collection.insert_one({"sequence": 2})
        second = await _next_delivery(second_source)
        assert second.payload["fullDocument"]["sequence"] == 2
        await second.ack()
        await second_source.close()
    finally:
        await collection.drop()
        await client.close()

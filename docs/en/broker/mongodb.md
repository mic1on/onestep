---
title: MongoDB | Broker
outline: deep
---

# MongoDB

`onestep-mongodb` provides deterministic collection polling, native MongoDB Change Stream, and acknowledged collection insert or stable-key upsert Sink.

## Installation

```bash
pip install onestep-mongodb
```

Requires Python 3.9+, `onestep>=1.9.0`, and `pymongo>=4.13`. Uses PyMongo native `AsyncMongoClient`, not Motor.

## Python Usage

```python
from onestep_mongodb import MongoDBConnector

mongo = MongoDBConnector(
    "mongodb://writer:secret@mongo-rs0/app?replicaSet=rs0",
    database="app",
    client_options={"serverSelectionTimeoutMS": 10_000},
)

polling = mongo.poll_collection(
    "events",
    cursor=("updated_at", "_id"),
    filter={"archived": False},
    batch_size=100,
    poll_interval_s=1.0,
    state=durable_cursor_store,
    state_key="events-poll",
)

changes = mongo.watch_collection(
    "events",
    pipeline=[{"$match": {"operationType": {"$in": ["insert", "update", "delete"]}}}],
    full_document="updateLookup",
    max_await_time_ms=1000,
    state=durable_cursor_store,
    state_key="events-change-stream",
)

sink = mongo.collection_sink(
    "events_archive",
    mode="upsert",
    keys=("event_id",),
    ordered=True,
    batch_size=1000,
)
```

The connector lazily creates a client and only closes the one it created. Injected clients are managed by the caller. Sources close their own query cursors or change streams; all `close()` methods are idempotent.

## YAML Configuration

Production environments should use a persistent cursor store. The example below uses a separate PostgreSQL cursor store plugin:

```yaml
resources:
  mongo:
    type: mongodb
    uri: "${MONGODB_URI}"
    database: app
    client_options:
      serverSelectionTimeoutMS: 10000

  cursor_db:
    type: postgres
    dsn: "${POSTGRES_DSN}"

  cursor_state:
    type: postgres_cursor_store
    connector: cursor_db
    table: onestep_cursor

  events_poll:
    type: mongodb_polling
    connector: mongo
    collection: events
    cursor: [updated_at, _id]
    filter:
      archived: false
    batch_size: 100
    poll_interval_s: 1
    state: cursor_state
    state_key: events-poll

  events_changes:
    type: mongodb_change_stream
    connector: mongo
    collection: events
    pipeline:
      - $match:
          operationType:
            $in: [insert, update, delete]
    full_document: updateLookup
    max_await_time_ms: 1000
    batch_size: 100
    poll_interval_s: 0.1
    state: cursor_state
    state_key: events-change-stream

  archive:
    type: mongodb_collection_sink
    connector: mongo
    collection: events_archive
    mode: upsert
    keys: [event_id]
    ordered: true
    batch_size: 1000
```

## Sources

### Polling

Polling uses ascending lexicographic cursor traversal. `_id` is always the final tie-breaker. Only the largest continuous acknowledged cursor is persisted. `fail()` skips the poison document and advances the continuous cursor; `retry()` and `release_unstarted()` invalidate the current generation and replay from the last committed cursor only after all stale deliveries complete. Late acknowledgments from invalidated generations are ignored.

Polling projections must retain every active cursor field, including the implicit `_id` tie-breaker. Invalid projections are rejected at source construction. Cursor state is updated in memory only after it is confirmed saved in the configured cursor store; save failure means the generation is replayable from the last persisted position. If a delivery is retried, no token in that fetch batch advances the cursor, including later confirmed deliveries.

Polling does not emit delete events. Only cursor-field-incrementing updates are seen; non-monotonic cursor field updates may be missed. For such events, use Change Stream.

### Change Stream

Change Stream requires a MongoDB replica set or sharded cluster; standalone is not supported. Deliveries carry the full raw change event:

```python
{
    "_id": {"...": "resume token"},
    "operationType": "update",
    "documentKey": {"_id": "..."},
    "fullDocument": {"...": "..."},
    "updateDescription": {"...": "..."},
}
```

The resume token is only advanced after consecutive delivery acknowledgments. If the resume token exceeds the oplog or becomes invalid, the source permanently fails and does not silently degrade to the current server position. To reset such a source: stop the worker, confirm the permanent history-lost error, delete or reset the source's `state_key`, then restart.

## Sink and Delivery Semantics

Sink accepts exactly one mapping or a non-empty sequence of mappings. A single sequence is split deterministically into chunks by `batch_size`, submitted sequentially, and waits for each MongoDB acknowledgment. The plugin rejects unacknowledged write concerns (`w=0`).

In Insert mode, a single mapping uses `insert_one`, and a sequence chunk uses `insert_many`. Safe replay is only possible when documents have a stable `_id` value; duplicate keys are permanent conflicts, not implicit successes. Upsert mode requires all configured stable keys, sends `UpdateOne` operations with key-equality filters and `$set` excluding key fields and immutable `_id`.

`ordered: true` is the conservative default. `ordered: false` provides higher throughput and reports multiple failures. Bulk operations or subsequent chunks may be partially committed. Non-idempotent writes are reported as UNCERTAIN with redacted index, code, count, and message.

onestep delivery is at-least-once. Sink output may be committed before source acknowledgment; crashes during this window may cause duplicate output. Multi-sink fan-out is not transactional. For duplicate handling, make handlers and downstream writes idempotent.

## Not Supported in Initial Release

The first release does not provide: database/cluster-level streams, transactions, Sink delete, replace, or update pipeline, schema validation or DDL, GridFS, aggregation or sharded polling, pre-image, expanded events, custom codec, or MongoDB-native cursor store.

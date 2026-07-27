# onestep-mongodb

`onestep-mongodb` provides deterministic collection polling, raw MongoDB change
streams, and acknowledged collection insert or stable-key upsert sinks for
`onestep`.

## Install

```bash
pip install onestep-mongodb
```

The plugin requires Python 3.9 or newer, `onestep>=1.7.1`, and
`pymongo>=4.13`. It uses PyMongo's native `AsyncMongoClient`; Motor is not used.

## Python

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

The connector creates its client lazily and closes only a client it owns. An
injected client remains owned by its caller. Sources close their own query cursor
or change stream; all `close()` methods are idempotent.

## Strict YAML

Production configurations use an explicit durable cursor store. This example
uses the separately configured PostgreSQL cursor-store plugin:

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: mongo-events

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

Polling and change streams can run with in-memory state for development, but that
state is lost on restart. Production restart guarantees require an explicit durable
`state` cursor-store resource. Without polling state, scanning starts from the
beginning; without a change-stream resume token, watching starts at the current
server position.

Cursor vectors and change-stream resume tokens are encoded through BSON Extended
JSON before they reach a generic cursor store. This preserves BSON values such as
`ObjectId`, datetimes, `Decimal128`, binary values, and timestamps in JSON-backed
stores.

## Sources

Polling uses ascending lexicographic keyset traversal. `_id` is always the final
tie-breaker. It persists only the greatest contiguous acknowledged cursor. A
terminal `fail()` skips the poison document and advances that contiguous cursor;
`retry()` and `release_unstarted()` invalidate the generation and replay from the
last committed cursor only after all stale deliveries finish. Late acknowledgements
from invalidated generations do nothing.

Polling does not emit deletes. It sees updates only if a cursor field increases,
and can miss non-monotonic cursor updates. Use change streams for workloads that
need those events.

Change streams require a MongoDB replica set or sharded cluster. A standalone
server is not supported. Deliveries retain the complete raw change event:

```python
{
    "_id": {"...": "resume token"},
    "operationType": "update",
    "documentKey": {"_id": "..."},
    "fullDocument": {"...": "..."},
    "updateDescription": {"...": "..."},
}
```

Change-stream deliveries contain the complete raw MongoDB change event. Update
streams request `full_document: updateLookup` by default. Project or reduce the
event in the application handler, not in YAML.

The resume token advances only after contiguous delivery acknowledgement. If a
resume token falls out of the oplog or is invalid, the source fails permanently;
it never silently falls back to the current server position. To reset such a
source, stop the worker, inspect the permanent history-lost error, deliberately
delete or reset only that source's `state_key`, then restart knowing the stream
begins at the current server position.

## Sinks and delivery semantics

Sinks accept exactly one mapping or a non-empty sequence of mappings. They split a
single sequence deterministically by `batch_size`, submit chunks sequentially, and
await every MongoDB acknowledgement. The plugin rejects unacknowledged write
concern (`w=0`).

Insert mode uses `insert_one` for one mapping and `insert_many` for sequence
chunks. It is replay-safe only when documents have stable `_id` values; duplicate
keys are permanent conflicts, not implicit success. Upsert mode requires all
configured stable keys and sends `UpdateOne` operations with key equality filters
and `$set` excluding key fields and immutable `_id`.

`ordered: true` is the conservative default. `ordered: false` can report multiple
item failures for more throughput. A bulk operation or later chunk may have
partially committed. For non-idempotent writes this is reported as uncertain,
preserving only redacted indexes, codes, counts, and messages.

onestep delivery is at-least-once. Sink output can commit before source
acknowledgement, and a crash in that interval can duplicate output. Multi-sink
fan-out is not transactional. Make handlers and downstream writes idempotent
where duplicates matter.

## Deferred features

Version 1 does not provide database- or cluster-wide streams, transactions, sink
deletes, replacement or update pipelines, schema validation or DDL, GridFS,
aggregation or partitioned polling, pre-images, expanded events, custom codecs,
or an automatically created MongoDB-backed cursor store.

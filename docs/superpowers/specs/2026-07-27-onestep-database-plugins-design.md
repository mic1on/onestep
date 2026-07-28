# Elasticsearch/OpenSearch, ClickHouse, and MongoDB Plugin Design

Date: 2026-07-27

Status: captain-approved design contract

## Purpose

This specification defines the first release of three independent onestep connector
plugins:

- `onestep-elasticsearch`, serving both Elasticsearch and OpenSearch;
- `onestep-clickhouse`;
- `onestep-mongodb`.

It freezes their public Python and strict YAML surfaces, data flow, lifecycle,
delivery, error, idempotency, compatibility, packaging, integration, testing, and
release contracts. It is the design authority for the later implementation work.

The plugins use the stable plugin API already present in `onestep>=1.7.1`. There is
no core runtime prerequisite and no change to `Source`, `Sink`, `Delivery`,
`Envelope`, runner retry behavior, resource registry helpers, reporter payloads,
control-plane protocols, runtime identity, or remote task controls.

## Scope And Delivery Shape

The work has four ownership tracks:

1. An Elasticsearch/OpenSearch plugin-local track owns only
   `plugins/onestep-elasticsearch/**`.
2. A ClickHouse plugin-local track owns only `plugins/onestep-clickhouse/**`.
3. A MongoDB plugin-local track owns only `plugins/onestep-mongodb/**`.
4. One shared integration track follows the three plugin-local tracks and owns all
   root workspace, lockfile, reliability, CI, documentation, changelog, integration
   harness, and bundled-worker changes.

The first three tracks may proceed independently after this contract is accepted.
The shared track starts only after all three plugin package surfaces are stable. No
plugin-local track changes root integration files.

Each plugin is a repo-local package published independently at version `0.1.0`.
Every package requires Python `>=3.9` and `onestep>=1.7.1`, registers through the
`onestep.resources` entry-point group, and supplies catalog metadata for every
resource handler. Plugin runtime code imports stable public names from `onestep`,
not private configuration helpers.

## Shared Runtime Contract

### Explicit batching and backpressure

All three sinks use the same first-release input contract:

- `Sink.send()` accepts either one `Mapping[str, Any]` or one non-empty
  `Sequence[Mapping[str, Any]]`.
- A mapping is a one-row or one-document logical batch.
- A sequence is one logical batch. The plugin splits it deterministically by its
  configured action-count, row-count, and, where applicable, byte limit.
- Strings, empty sequences, mixed sequences, and non-mapping items are permanent
  payload errors. The plugin rejects them before its first network call.
- `send()` awaits every backend chunk acknowledgement and returns only after every
  item in every chunk is acknowledged.
- Chunks within one logical `send()` are submitted sequentially. Separate task
  deliveries may send concurrently up to the onestep task concurrency and client
  pool limits.
- There is no timer-based coalescing across independent `send()` calls, hidden
  flush queue, or core `BatchSink.send_many()` API in this release.
- The caller's payload is not retained after `send()` resolves. Memory is bounded
  by the caller payload plus one serialized or normalized chunk.

This awaited contract supplies direct backpressure. onestep acknowledges the source
only after all selected sinks return, and the runtime stops fetching when task
concurrency is exhausted. No second plugin semaphore or buffering layer is added in
v1.

### At-least-once and partial commits

onestep remains at-least-once. A sink can commit output before source `ack()` fails,
a process can stop between sink acknowledgement and source acknowledgement, and
multi-sink fan-out is not transactional. Duplicate output is therefore possible.

Bulk operations are also not transactional across chunks, and some backends can
partially commit within a chunk. Each plugin must:

- inspect backend acknowledgement at item or chunk granularity;
- retain redacted item index, backend code/status, identifier, and normalized
  reason in a typed cause where the backend exposes those details;
- distinguish permanent payload failures from transient backend failures;
- never put credentials, complete documents, or complete rows in exception text,
  logs, topology descriptors, or control-plane metadata;
- stop submitting later chunks after a chunk fails;
- document that an explicit task retry may repeat earlier committed items or
  chunks.

Once any earlier item or chunk has committed, a later otherwise retryable failure
is reported as `ConnectorErrorKind.UNCERTAIN` unless replay of the entire logical
payload is demonstrably idempotent for that sink mode. `UNCERTAIN` prevents the
runner's built-in transient retry from blindly replaying committed work. It does
not claim that the write failed; it means the final commit set is unknown.

### Common error categories

Plugin-local resilience modules map client exceptions to the stable
`ConnectorOperationError` contract:

| Kind | Meaning in these plugins | Runtime implication |
| --- | --- | --- |
| `DISCONNECTED` | Connection or DNS failure known to occur before submission, or server selection failure | Automatically retryable by the runner |
| `THROTTLED` | Explicit backend overload or rate limiting | Automatically retryable by the runner |
| `TRANSIENT` | Retryable server unavailability with no known partial commit | Automatically retryable by the runner |
| `UNCERTAIN` | Submission may have committed, or a logical batch partially committed | Not automatically replayed |
| `PERMANENT` | Invalid payload, schema/type mismatch, conflict, or non-recoverable cursor state | Not automatically retried |
| `MISCONFIGURED` | Authentication, TLS, resource, database/table/index, or invalid option configuration | Requires operator correction |

Backend-specific rules below override the generic examples when more precise
evidence is available.

### Lifecycle and ownership

Clients are opened lazily so strict YAML checking does not contact external
services. An internally constructed client is owned by its connector and closed
exactly once. A caller-injected test or application client is not owned and is not
closed by the plugin. Connector, source, sink, cursor, and stream `close()` methods
are idempotent.

onestep opens each unique named or task resource once and closes resources in
reverse order after runners drain. Sinks contain no hidden pending buffers at
close. MongoDB sources own and close their query cursor or change stream, while the
MongoDB connector owns the client.

## `onestep-elasticsearch`

### Product boundary

One package supports both Elasticsearch and OpenSearch through the same resource
and Python surface. The distribution selector is
`auto | elasticsearch | opensearch`. Version 1 implements one asynchronous bulk
sink for a static index and reserves a clean path for future search sources. It
does not ship a source.

The shared compatibility boundary is deliberately narrow: HTTP(S), common
Basic/API-key/Bearer authentication, custom headers, TLS, `GET /`, and
`POST /_bulk`. The plugin owns this REST boundary through `httpx>=0.27`; it does not
depend on either vendor's Python client or imply that one vendor client's version
defines compatibility with the other distribution.

### Public Python surface

```python
from onestep_elasticsearch import ElasticsearchConnector

search = ElasticsearchConnector(
    ["https://search-1:9200", "https://search-2:9200"],
    distribution="auto",
    username="ingest",
    password="secret",
    verify_certs=True,
    ca_certs="/etc/ssl/search-ca.pem",
    request_timeout_s=30.0,
)

sink = search.bulk_sink(
    index="events-v1",
    operation="index",
    id_field="event_id",
    chunk_size=500,
    max_chunk_bytes=5_000_000,
    refresh=False,
)
```

The package exports:

- `ElasticsearchConnector`;
- `ElasticsearchBulkSink`;
- `ElasticsearchBulkError`;
- `ElasticsearchBulkItemError`;
- `register`;
- `register_resources`.

The connector accepts an optional injected asynchronous HTTP transport/client for
unit tests. Its Python-only `max_retries` sink/connector option defaults to `2`; it
is not a YAML field in v1.

`ElasticsearchBulkSink.send()` follows the shared mapping-or-sequence contract.
Each mapping is the complete `_source`. When configured, `id_field` supplies `_id`
but remains in `_source`. `index` and `operation` are static sink configuration;
YAML does not become a document-routing or action DSL.

### Strict YAML surface

```yaml
resources:
  search:
    type: elasticsearch
    hosts: ["${SEARCH_URL}"]
    distribution: auto
    username: "${SEARCH_USERNAME}"
    password: "${SEARCH_PASSWORD}"
    verify_certs: true
    ca_certs: "${SEARCH_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}"
    request_timeout_s: 30

  events:
    type: elasticsearch_bulk_sink
    connector: search
    index: events-v1
    operation: index
    id_field: event_id
    chunk_size: 500
    max_chunk_bytes: 5000000
    refresh: false
```

`elasticsearch` is a connector catalog resource with these allowed fields:

| Field | Contract |
| --- | --- |
| `type` | Must be `elasticsearch` |
| `hosts` | Required non-empty HTTP(S) URL string or string list |
| `distribution` | `auto`, `elasticsearch`, or `opensearch`; default `auto` |
| `username`, `password` | Optional Basic credentials; both must appear together; secret |
| `api_key` | Optional API-key credential; secret |
| `bearer_token` | Optional Bearer credential; secret |
| `headers` | Optional secret mapping of custom headers |
| `verify_certs` | Boolean; default `true` |
| `ca_certs` | Optional CA bundle path |
| `client_cert`, `client_key` | Optional client certificate/key; key is secret |
| `request_timeout_s` | Positive number; default `10.0` |

Configuration may use no auth mode or exactly one of Basic, API key, or Bearer.
The Basic pair counts as one mode. Strict validation rejects unknown fields,
partial Basic credentials, multiple auth modes, invalid host schemes, and
non-positive numeric values without opening a network connection.

`elasticsearch_bulk_sink` is a sink catalog resource with these allowed fields:

| Field | Contract |
| --- | --- |
| `type` | Must be `elasticsearch_bulk_sink` |
| `connector` | Required reference resolving to `ElasticsearchConnector` |
| `index` | Required non-empty static index |
| `operation` | `index` or `create`; default `index` |
| `id_field` | Optional payload field used as `_id` |
| `chunk_size` | Positive action count; default `500` |
| `max_chunk_bytes` | Positive serialized NDJSON limit; default `5_000_000` |
| `refresh` | `false`, `true`, or `wait_for`; default `false` |
| `pipeline` | Optional static ingest pipeline name |

Its topology fields are `index`, `operation`, and `chunk_size`. Catalog metadata
and strict allowed fields must match exactly.

### Data flow and acknowledgement

The connector lazily creates one pooled `httpx.AsyncClient` and selects configured
hosts round-robin. `distribution: auto` may inspect `GET /`, including
`version.distribution` and the tagline, but must not require the Elastic-only
`X-Elastic-Product` header. There is no node sniffing.

For each sink chunk:

1. Serialize one bulk metadata line and one source line per document with
   `json.dumps`.
2. Set `_index`, optional `_id`, and the static `index` or `create` action.
3. End every NDJSON request body with a newline and use
   `Content-Type: application/x-ndjson`.
4. Split before either `chunk_size` or `max_chunk_bytes` would be exceeded.
5. Reject a single action larger than `max_chunk_bytes` as `PERMANENT` without
   sending it.
6. POST `/_bulk` with only common query parameters.
7. Parse every response item and return only when each status is 2xx. HTTP 200 with
   `errors: true` is a failed chunk.

The request does not contain document `_type` or vendor compatibility media types.
Response parsing tolerates unknown fields and normalizes the common `errors`,
`items`, item `status`, and nested error `type`/`reason` shapes.

### Errors and idempotency

The sink internally retries only the failed item subset for statuses
429/502/503/504, with bounded exponential backoff and jitter, up to the internal
retry limit. It does not retry successful items within that internal loop.

- `httpx.ConnectError` before submission is `DISCONNECTED`.
- 429 is `THROTTLED`.
- A retryable server status with no success in the logical send is `TRANSIENT`.
- Malformed documents and item-level 4xx failures are `PERMANENT` except where
  authentication or configuration makes `MISCONFIGURED` more accurate.
- Bad credentials, TLS, or index configuration is `MISCONFIGURED`.
- Read timeout or connection loss after transmission is `UNCERTAIN`.
- Create conflicts are permanent.

If some items have succeeded and retryable failures remain, `create` and auto-ID
`index` surface `UNCERTAIN`. Only `index` with a deterministic `id_field` may remain
`TRANSIENT`, because replaying the whole logical payload converges to the same final
documents. Stable IDs are the required mitigation when duplicates matter.

### Compatibility boundary and future sources

The v1 common subset contains `index` and `create`, `_index`, `_id`, standard NDJSON,
and `refresh` and `pipeline` parameters only where the identical compatibility test
passes on both distributions.

The connector keeps request, authentication, host selection, response
normalization, and error classification in reusable
`request_json()`/`request_ndjson()` primitives behind an internal distribution
adapter. The resource names `elasticsearch_search_after` and
`elasticsearch_scroll` are reserved but are not registered in v1.

A later `search_after` source should require deterministic sorting with a unique
tie-breaker and persist only the last contiguous acknowledged sort vector. A later
source must also document index-mutation caveats. A later scroll source remains a
separate class in this package because it owns server-side cursor, clear-scroll,
unsafe-fetch, and release lifecycle. PIT stays distribution-specific behind the
adapter.

### Deferred Elasticsearch/OpenSearch features

The following are explicitly outside v1: Elastic Cloud `cloud_id`, node sniffing,
AWS SigV4 and Amazon OpenSearch Service integration, data streams, aliases and
`require_alias`, ingest pipeline creation, ILM/ISM, templates and mappings,
update/delete/script actions, dynamic index/action fields, payload templates, PIT,
SQL, all search sources, and distribution-specific security administration.

## `onestep-clickhouse`

### Product boundary

The first release is an async table-insert sink for event, log, and analytics rows.
It performs acknowledged inserts into an existing table. It does not create or
migrate databases/tables, model ClickHouse engines, query data, or implement
upsert/mutation semantics.

The client is `clickhouse-connect[async]>=0.8` using
`clickhouse_connect.get_async_client`. This vendor-supported async facade avoids a
custom executor and keeps task concurrency plus the client pool as the only
concurrency controls.

### Public Python surface

```python
from onestep_clickhouse import ClickHouseConnector

clickhouse = ClickHouseConnector(
    dsn="https://writer:secret@clickhouse:8443/analytics",
    client_options={"connect_timeout": 10, "send_receive_timeout": 30},
)

sink = clickhouse.table_sink(
    table="events",
    columns=("event_id", "occurred_at", "kind", "payload"),
    batch_size=1000,
    settings={"async_insert": 0},
)
```

The package exports `ClickHouseConnector`, `ClickHouseTableSink`,
`ClickHousePayloadError`, `register`, and `register_resources`. The connector
accepts an injected asynchronous client for tests and otherwise creates its client
lazily.

`columns` is optional. With configured columns, every row must contain every named
column and no unexpected keys. Without configured columns, the first row's
insertion order fixes the column order for that logical `send()`, and every later
row must have exactly the same key set. Mappings are normalized to row sequences
and passed to:

```python
await client.insert(
    table,
    rows,
    column_names=columns,
    settings=settings,
)
```

### Strict YAML surface

```yaml
resources:
  analytics:
    type: clickhouse
    dsn: "${CLICKHOUSE_DSN}"
    client_options:
      connect_timeout: 10
      send_receive_timeout: 30

  events:
    type: clickhouse_table_sink
    connector: analytics
    table: events
    columns: [event_id, occurred_at, kind, payload]
    batch_size: 1000
    settings:
      async_insert: 0
```

`clickhouse` is a connector catalog resource. Its allowed fields are `type`, a
required secret non-empty `dsn`, and an optional secret `client_options` mapping.
Strict validation accepts ClickHouse and HTTP(S) DSN forms supported by the client
without making a network connection. Separate host, user, or password catalog
fields are not advertised because the builder does not accept them.

`clickhouse_table_sink` is a sink catalog resource with:

| Field | Contract |
| --- | --- |
| `type` | Must be `clickhouse_table_sink` |
| `connector` | Required reference resolving to `ClickHouseConnector` |
| `table` | Required non-empty existing table name |
| `columns` | Optional non-empty unique string list |
| `batch_size` | Positive row count; default `1000` |
| `settings` | Optional mapping passed to insert |

Its topology fields are `table`, `columns`, and `batch_size`. Unknown fields,
invalid references, empty or duplicate columns, invalid batch sizes, and non-mapping
settings are strict validation errors.

### Data flow, errors, and idempotency

A mapping becomes one row and a sequence is chunked by `batch_size`. Empty, mixed,
missing-column, extra-column, and inconsistent-key payloads fail permanently before
the first network call. Each chunk is inserted and awaited sequentially.

A successful `send()` means that every insert received an acknowledged server
response. Fire-and-forget async inserts are not permitted. If users explicitly set
`async_insert`, strict construction requires `wait_for_async_insert=1`; any setting
that permits acknowledgement before insert completion is rejected.

- Connect and DNS failures are `DISCONNECTED`.
- Server overload and too-many-queries are `THROTTLED`.
- Retryable server unavailability is `TRANSIENT` when no earlier chunk committed.
- Authentication, unknown database/table, and invalid settings are
  `MISCONFIGURED`.
- Type and column errors are `PERMANENT`.
- A receive timeout after submission is `UNCERTAIN`.

The typed cause preserves the ClickHouse error code and a redacted message. A later
chunk failure after an earlier acknowledged chunk is `UNCERTAIN`, even if the later
error would otherwise be transient. No uncertain insert is automatically replayed.

ClickHouse idempotency is schema-dependent. Where duplicates matter, deployments
should use a stable event key and a `ReplacingMergeTree` or another dedup-aware
table design. The plugin does not generate a generic deduplication token: identical
batches can be valid, and chunk boundaries can change across retries.

### Deferred ClickHouse features

The following are explicitly outside v1: automatic timed coalescing, DDL and
migrations, query sources, streaming formats, Arrow/DataFrame-specialized APIs,
schema inference or coercion, distributed-table routing, plugin-generated dedup
tokens, and mutation or upsert semantics.

## `onestep-mongodb`

### Product boundary

One async connector provides three v1 resources:

1. deterministic incremental collection polling;
2. a resumable collection change-stream source emitting raw change events;
3. a collection sink supporting insert and stable-key upsert modes.

The client is `pymongo>=4.13` and its native `AsyncMongoClient`. Motor is not used
because it is on a deprecated migration path, and synchronous PyMongo is not
wrapped in the default executor because long-running change-stream operations would
occupy threads and complicate cancellation.

Change streams require a replica set or sharded cluster. A standalone MongoDB
server is not a supported change-stream deployment.

### Public Python surface

```python
from onestep_mongodb import MongoDBConnector

mongo = MongoDBConnector(
    "mongodb://writer:secret@mongo-rs0/app?replicaSet=rs0",
    database="app",
    client_options={"serverSelectionTimeoutMS": 10000},
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

The package exports:

- `MongoDBConnector`;
- `MongoDBPollingSource` and `MongoDBPollingDelivery`;
- `MongoDBChangeStreamSource` and `MongoDBChangeStreamDelivery`;
- `MongoDBCollectionSink`;
- `MongoDBPayloadError`;
- `register` and `register_resources`.

The connector accepts an injected `AsyncMongoClient` for tests. It creates one
owned client otherwise, selects the configured database once, and resolves
collections cheaply.

### Strict YAML surface

The production example explicitly configures durable state:

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

  events_cursor:
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
    state: events_cursor
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
    state: events_cursor
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

`mongodb` is a connector catalog resource with `type`, required secret non-empty
`uri`, required non-empty `database`, and optional secret `client_options` mapping.
Strict validation rejects unacknowledged write concern (`w=0`) because a v1 sink
must establish backend acknowledgement.

`mongodb_polling` is a source catalog resource with:

| Field | Contract |
| --- | --- |
| `type` | Must be `mongodb_polling` |
| `connector` | Required reference resolving to `MongoDBConnector` |
| `collection` | Required non-empty collection |
| `cursor` | Unique non-empty string list; default `[_id]` |
| `filter` | Optional query mapping |
| `projection` | Optional projection mapping |
| `batch_size` | Positive; default `100` |
| `poll_interval_s` | Non-negative; default `1.0` |
| `state` | Optional reference implementing the cursor-store capability |
| `state_key` | Optional persistent key override |
| `initial_cursor` | Optional JSON cursor value used when no state is stored |

Its topology fields are `collection`, `cursor`, `batch_size`, and
`poll_interval_s`. If `_id` is explicitly present in `cursor`, strict validation
requires it to be the final component; otherwise the effective cursor appends
`_id` as the deterministic tie-breaker.

`mongodb_change_stream` is a source catalog resource with:

| Field | Contract |
| --- | --- |
| `type` | Must be `mongodb_change_stream` |
| `connector` | Required reference resolving to `MongoDBConnector` |
| `collection` | Required non-empty collection |
| `pipeline` | Optional JSON list of aggregation stages |
| `full_document` | Supported PyMongo option; default `updateLookup` |
| `max_await_time_ms` | Positive; default `1000` |
| `batch_size` | Positive; default `100` |
| `poll_interval_s` | Non-negative; default `0.1` |
| `state` | Optional reference implementing the cursor-store capability |
| `state_key` | Optional persistent key override |

Its topology fields are `collection`, `full_document`, `batch_size`, and
`max_await_time_ms`.

`mongodb_collection_sink` is a sink catalog resource with:

| Field | Contract |
| --- | --- |
| `type` | Must be `mongodb_collection_sink` |
| `connector` | Required reference resolving to `MongoDBConnector` |
| `collection` | Required non-empty collection |
| `mode` | `insert` or `upsert`; default `insert` |
| `keys` | Unique non-empty string list required for `upsert` |
| `ordered` | Boolean; default `true` |
| `batch_size` | Positive; default `1000` |

Its topology fields are `collection`, `mode`, `keys`, and `batch_size`. Pipeline
and initial-cursor catalog fields use `type="json"`, while strict validators still
enforce their list or mapping shape. All resource handlers reject unknown fields
and invalid cross-field combinations at the full resource path.

### Durable state contract

Both MongoDB sources may run without a cursor store for development. Production
restart guarantees require an explicit durable `state` resource, and every strict
production example and README production path must show one. The plugin does not
silently create a MongoDB-backed state collection.

- Polling without persisted state starts from `initial_cursor` when supplied and
  otherwise from the beginning of the collection.
- A change stream without a persisted resume token starts at the current server
  position, not from historical collection contents.
- In-memory progress exists only for the life of the source and provides no
  restart guarantee.
- Cursor components and resume tokens are encoded with `bson.json_util` Extended
  JSON before calling a generic cursor store, then decoded after loading. This
  preserves ObjectId, datetime, Decimal128, binary, and timestamp values through
  existing JSON-backed stores.
- A resume token that has fallen out of the oplog or is invalid is never silently
  discarded. The source fails permanently and requires operator action or an
  explicit state reset.

### Polling data flow and acknowledgement

Polling is deterministic ascending keyset traversal, not CDC:

1. Use the configured cursor and make `_id` the final unique tie-breaker.
2. Build the standard lexicographic `$or` ladder for composite cursors.
3. Combine that keyset predicate with the configured filter under `$and`.
4. Sort all effective cursor fields ascending and limit results to
   `min(runtime_limit, batch_size)`.
5. Wrap each result in a delivery with a local monotonic sequence and its Extended
   JSON-compatible cursor token.
6. Persist only the greatest contiguous acknowledged token; an out-of-order ack
   never advances state past a gap.

`fail()` terminally skips a poison document by marking it complete in the same
contiguous tracker. `retry(delay_s)` waits, invalidates the current generation,
clears uncommitted scan state, and reopens from the last committed cursor.
`release_unstarted()` performs the same generation invalidation without committing.
Late acks from an invalidated generation are no-ops.

The source tracks outstanding deliveries by generation. After invalidation it
fetches no replacement generation until every stale delivery reaches
ack/retry/fail/cancellation. Stale terminal callbacks only decrement the count;
they never advance durable state. This prevents replay from beginning while a stale
handler can still produce downstream effects.

`fetch_is_cancel_safe=False`. A completed fetch after pause, drain, or shutdown
must release every returned-but-unstarted delivery. The next query starts from the
committed cursor.

Deletes are invisible to polling. In-place updates are visible only when a cursor
field increases, and non-monotonic cursor changes can be missed. Workloads needing
those events must use change streams.

### Change-stream data flow and acknowledgement

The delivery payload is the complete raw MongoDB change event in `Envelope.body`.
This preserves operation type, resume metadata, delete document keys, update
metadata, and `fullDocument` where the server supplies it. The plugin does not offer
a reduced `fullDocument`-only v1 mode; handlers own payload projection. Redacted
connector, database, and collection context is added to `Envelope.meta`.

The source defaults `full_document` to `updateLookup`. On first open it loads and
decodes the stored resume token, calls `collection.watch(..., resume_after=token)`
when present, and otherwise starts at the current server position. `try_next()` may
wait up to `max_await_time_ms`, and each fetch returns no more than the smaller of
the runtime limit and `batch_size`.

Resume-token mappings are associated with local sequence IDs because BSON token
dictionaries are not hashable. Durable state advances only through the last
contiguous acknowledged token; driver-level automatic resume may continue the live
stream but does not move persisted state ahead of onestep acknowledgement.

`retry()` and `release_unstarted()` invalidate the generation, close the active
stream, clear uncommitted tokens, and reopen from the last committed token after
all stale deliveries terminate. Late stale acks are ignored. This deliberately
duplicates later events from the invalidated batch rather than risk losing the
failed event. `fail()` advances the token as a terminal skip and records only the
operation type and document key in failure metadata.

The source sets `fetch_is_cancel_safe=False`. Close interrupts a pending stream and
is idempotent. Resumable change-stream labels are `TRANSIENT`; server selection and
network fetch failures are `DISCONNECTED`; authentication/configuration errors are
`MISCONFIGURED`; `ChangeStreamHistoryLost` and invalid resume tokens are
`PERMANENT` with an operator-action message.

### Sink data flow, errors, and idempotency

Insert mode calls `insert_one` for one mapping and chunked `insert_many` for a
sequence. It is replay-safe only when documents carry stable `_id` values. A
duplicate-key error is a permanent conflict, not implicit success, because the
existing document may differ.

Upsert mode requires every key in every document. Each document becomes:

```python
UpdateOne(
    filter={key: document[key] for key in keys},
    update={"$set": non_key_fields},
    upsert=True,
)
```

Key fields remain in the equality filter, and immutable `_id` is excluded from
`$set`. Chunks are sent with `bulk_write`. `ordered=True` is the conservative
default and limits later writes after the first error; `ordered=False` is available
for throughput and reports every item failure.

The sink returns only after an acknowledged result. PyMongo may perform its safe
driver-level retryable writes. Once an error reaches the plugin:

- connect-before-send is `DISCONNECTED`;
- server throttling is `THROTTLED`;
- schema, key, payload, and duplicate-key errors are `PERMANENT`;
- authentication and configuration errors are `MISCONFIGURED`;
- post-submit timeout and `AutoReconnect` are `UNCERTAIN`.

`BulkWriteError.details` is preserved in a typed redacted cause with failed item
indexes and codes. A later chunk failure after an earlier acknowledgement is
`UNCERTAIN` unless every operation is a stable-key upsert. Uncertain inserts are
not automatically replayed; stable-key upserts may be replayed by an explicit task
policy because they converge on the configured key.

### Deferred MongoDB features

The following are explicitly outside v1: database-wide or cluster-wide change
streams, transactions, sink deletes, replacement or updater pipelines, schema
validation and DDL, GridFS, aggregation polling, partitioned polling, pre-images,
expanded events, custom codecs, and an automatically created MongoDB-backed cursor
store.

## Package And Integration Ownership

Each plugin uses Hatch with a `src/` layout, test/dev extras, a README, and a single
entry point:

| Package | Runtime dependency | Entry point |
| --- | --- | --- |
| `onestep-elasticsearch` | `httpx>=0.27` | `elasticsearch = "onestep_elasticsearch:register"` |
| `onestep-clickhouse` | `clickhouse-connect[async]>=0.8` | `clickhouse = "onestep_clickhouse:register"` |
| `onestep-mongodb` | `pymongo>=4.13` | `mongodb = "onestep_mongodb:register"` |

All also require `onestep>=1.7.1`, Python `>=3.9`, and pytest/pytest-asyncio test
extras. Plugin-local packages contain their connector, resources, resilience,
public exports, README, unit tests, runtime contract tests where applicable, and
optional live tests. MongoDB additionally contains its Extended JSON state codec.

The package layouts are:

```text
plugins/onestep-elasticsearch/
  pyproject.toml
  README.md
  src/onestep_elasticsearch/{__init__,connector,resources,resilience}.py
  tests/test_elasticsearch_{connector,plugin,resilience}.py
  tests/integration/test_elasticsearch_live.py

plugins/onestep-clickhouse/
  pyproject.toml
  README.md
  src/onestep_clickhouse/{__init__,connector,resources,resilience}.py
  tests/test_clickhouse_{connector,plugin,resilience}.py
  tests/integration/test_clickhouse_live.py

plugins/onestep-mongodb/
  pyproject.toml
  README.md
  src/onestep_mongodb/{__init__,connector,resources,resilience,state_codec}.py
  tests/test_mongodb_{polling,change_stream,sink,plugin,resilience}.py
  tests/test_mongodb_runtime_contract.py
  tests/integration/test_mongodb_live.py
```

The final shared integration track owns these cross-repository surfaces:

- add `elasticsearch`, `clickhouse`, and `mongodb` root extras;
- add all three packages to `all`, `dev`, and `integration`, plus uv workspace
  members and sources;
- regenerate `uv.lock` once and pass `uv lock --check`;
- add all three isolated plugin suites to `scripts/run-reliability-checks.sh` and
  its assertion test;
- add independent plugin workflows across Python 3.9-3.12 that run tests, build
  wheel and sdist artifacts, pass `twine check`, and gate trusted publishing on
  successful validation;
- add the live integration services and explicit test paths;
- add all three packages to the batteries-included worker image;
- update the root connector/extras table, strict YAML resource documentation,
  onestep connector skill reference, examples, and changelog.

Catalog metadata is sufficient for generic topology discovery. No control-plane UI
or protocol coordination and no `onestep-control-plane` release are required.

## Test Contract

### Isolated unit and runtime tests

Every resource handler is tested through the real `onestep.resources` entry point,
including catalog metadata, valid strict YAML, unknown fields, missing fields,
cross-field rules, reference type checks, and secret redaction. All client tests use
injected fakes and require no live service.

Elasticsearch/OpenSearch tests cover exact NDJSON, mapping and sequence validation,
action and byte chunking, oversized actions, both auth surfaces, response shapes,
partial item success, fake-transport 429 handling, retry-only-failed subsets,
uncertainty, client ownership, idempotent close, and a `OneStepApp` contract proving
source ack follows completed bulk acknowledgement.

ClickHouse tests cover lazy client ownership, configured and inferred columns,
mapping and sequence normalization, all invalid row shapes, exact chunk calls,
acknowledged async-insert enforcement, error-code classification, partial
multi-chunk failure, no submission of later chunks, close, and source ack ordering.

MongoDB polling tests cover scalar/composite keyset queries, `_id` tie-breaking,
filter combination, Extended JSON round trips, out-of-order acknowledgement gaps,
restart, retry/release generation replay, stale ack no-ops, terminal fail, runtime
fetch limits, backpressure, and close. Change-stream tests cover watch options, raw
event envelopes, start-now without a token, `resume_after`, contiguous ack,
resumable reopen, generation reset, stale callbacks, history loss, and unsafe-fetch
pause/drain/shutdown. Sink tests cover insert one/many, chunking, upsert filters and
keys, immutable `_id`, ordered modes, unacknowledged write concern, duplicate keys,
partial bulk errors, uncertain timeouts, ownership, close, and source ack ordering.

### Live compatibility matrices

Live suites are marked `integration`, skip when their environment variable is not
present, and are optional for ordinary local unit work. They are required before
publishing the affected `0.1.0` package.

| Plugin | Required matrix | Required behavior |
| --- | --- | --- |
| Elasticsearch/OpenSearch | Latest Elasticsearch 8.x patch, current Elasticsearch 9.x, latest OpenSearch 2.x, current OpenSearch 3.x | Open/auth, one/list writes, stable-ID replay, chunking, create conflict, partial mapping failure, refresh visibility, identical golden error fixtures |
| Elasticsearch/OpenSearch | Optional Elasticsearch 7.17 smoke | Same typeless bulk request |
| ClickHouse | Supported LTS and current release | One/many rows, timestamp/nullable values, chunking, missing table/type errors, immediate visibility |
| MongoDB | Single-node replica set through `ONESTEP_MONGODB_URI` | Polling restart with ObjectId/datetime, insert/upsert replay, change-stream insert/update/delete, resume after plugin restart |
| MongoDB | Optional resilience profile | Forced primary stepdown |

Elasticsearch and OpenSearch use the same behavioral suite and golden common error
fixtures. Their compatibility is not inferred from a Python client version.
ClickHouse uses `ONESTEP_CLICKHOUSE_DSN`. Search live tests use
`ONESTEP_ELASTICSEARCH_URL` and `ONESTEP_OPENSEARCH_URL`. MongoDB integration must
initialize a replica set rather than use a standalone container. Elasticsearch and
OpenSearch run as separate compatibility-matrix jobs rather than simultaneous
services in the default integration stack.

The shared validation gate runs each plugin suite in an isolated pytest process,
then the full non-integration reliability checks, strict YAML examples, package
builds, and `twine check`. CI and plugin workflows cover Python 3.9, 3.10, 3.11,
and 3.12.

## Documentation Contract

Each plugin README includes installation, public Python usage, strict YAML usage,
client lifecycle, and the shared at-least-once warning. It must state that sink
output can commit before source acknowledgement and that multi-sink fan-out is not
transactional.

The Elasticsearch README additionally documents the distribution selector, common
HTTP/auth/TLS boundary, compatibility matrix, payload shape, stable-ID guidance,
partial success, and reserved future sources. The ClickHouse README documents
column rules, task concurrency and client-pool tuning, acknowledged async inserts,
duplicate semantics, and a `ReplacingMergeTree` example as guidance rather than
automatic DDL. The MongoDB README documents replica-set requirements, raw event
shape, Extended JSON state, the production durable-state requirement, polling
limitations, resume-token reset procedure, acknowledged write concern, and
insert/upsert idempotency.

## Release Sequence

1. Treat this approved specification as the resource/type/field and behavior
   contract; no core code is a prerequisite.
2. Complete the three plugin-local packages independently without touching shared
   root files.
3. Land the single shared integration track after all three package surfaces are
   stable, regenerating shared metadata once.
4. Pass isolated unit/runtime suites, full non-integration reliability gates,
   strict examples, package builds, and Python 3.9-3.12 checks.
5. Pass the required Elasticsearch/OpenSearch, ClickHouse, and MongoDB live
   compatibility matrices.
6. Publish each plugin at `0.1.0`. Release root extras or a bundled worker that
   resolves published plugin dependencies only after those package versions are
   available.

Core package metadata and documentation may change in the integration track, but
core runtime behavior and stable APIs do not. A core release follows the existing
version, changelog, lockfile, and tag policy only if the root package metadata is
published. The plugin packages remain independently releasable.

## Acceptance Criteria

This design is satisfied when all of the following are true:

- the stable core runtime remains unchanged;
- the four-track ownership boundary is preserved;
- all sinks accept only a mapping or non-empty mapping sequence and await every
  chunk acknowledgement with direct backpressure;
- partial commits produce typed, redacted evidence and `UNCERTAIN` whenever whole
  payload replay is not demonstrably idempotent;
- one `onestep-elasticsearch` package supports the approved Elasticsearch and
  OpenSearch common boundary and leaves source extension points unregistered;
- ClickHouse inserts are async, chunked, and server-acknowledged;
- MongoDB polling and change streams persist only contiguous acknowledgement,
  generation resets replay from committed state, and raw change events default to
  `updateLookup`;
- MongoDB works without durable state for development while production restart
  guarantees and production examples require an explicit state resource;
- strict YAML catalogs and validators expose exactly the fields in this document;
- unit, runtime, package, Python-version, and live compatibility gates pass before
  the corresponding `0.1.0` release;
- all explicitly deferred features remain out of v1.

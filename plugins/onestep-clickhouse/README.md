# onestep-clickhouse

`onestep-clickhouse` provides an acknowledged asynchronous ClickHouse table sink
for [onestep](https://github.com/mic1on/onestep). It inserts rows into an existing
table; it does not create or migrate ClickHouse schema.

## Installation

```bash
pip install onestep-clickhouse
```

Python 3.9 or newer and `onestep>=1.7.1` are required.

## Python usage

```python
from onestep_clickhouse import ClickHouseConnector

clickhouse = ClickHouseConnector(
    dsn="https://writer:secret@clickhouse:8443/analytics",
    client_options={
        "connect_timeout": 10,
        "send_receive_timeout": 30,
    },
)

sink = clickhouse.table_sink(
    table="events",
    columns=("event_id", "occurred_at", "kind", "payload"),
    batch_size=1000,
    settings={"async_insert": 0},
)
```

The connector creates its async client lazily on the first insert. A connector
closes a client it created, while a client injected by an application remains
owned by the caller. Closing a connector is idempotent.

## Strict YAML usage

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: clickhouse-writer

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

tasks: []
```

Validate long-lived configuration with `onestep check --strict worker.yaml`.
The `dsn` and `client_options` catalog fields are secret metadata and are not
included in topology descriptors.

## Rows and columns

Each sink send accepts one mapping or an explicitly non-empty sequence of
mappings. Strings, empty sequences, mixed sequences, and non-mapping items are
rejected before the first network call.

When `columns` is configured, every row must contain exactly those keys. Values
are ordered according to the configured column sequence. When `columns` is
omitted, the first mapping's insertion order fixes the columns for that logical
send, and every later mapping must have exactly the same key set. The plugin does
not infer database schema or coerce values.

`batch_size` defaults to 1000 rows. A larger logical batch is split into chunks,
and each chunk is inserted and awaited sequentially. The sink has no timer,
hidden flush queue, or cross-send batching, so task concurrency and the
ClickHouse client pool provide the concurrency controls. Tune task concurrency
and client pool limits together for the server's capacity.

## Acknowledged async inserts

A successful send means every chunk received an acknowledged server response.
Fire-and-forget async inserts are rejected. If `async_insert` is enabled, the
settings must also include `wait_for_async_insert: 1`:

```yaml
settings:
  async_insert: 1
  wait_for_async_insert: 1
```

This awaited contract applies direct backpressure: onestep does not acknowledge
the source delivery until the selected sink finishes.

## Delivery and duplicate semantics

The sink awaits every ClickHouse insert chunk and has no hidden queue. A crash after
ClickHouse acknowledges a chunk but before onestep acknowledges the source can
duplicate rows. A later chunk failure is reported as uncertain because earlier
chunks remain committed. Idempotency depends on table design; use stable event keys
and a dedup-aware engine such as `ReplacingMergeTree` when duplicates matter.

Multi-sink fan-out is not transactional. If a later sink fails, ClickHouse writes
from an earlier successful sink call are not rolled back. An explicit task retry
may repeat already committed rows or chunks.

For example, a table can retain a stable event key and version for eventual
replacement:

```sql
CREATE TABLE events
(
    event_id String,
    version DateTime64(3),
    payload String
)
ENGINE = ReplacingMergeTree(version)
ORDER BY event_id;
```

This is deployment guidance only. The plugin does not execute DDL or generate a
deduplication token, and ClickHouse replacement behavior depends on the chosen
engine and query strategy.

## Deferred features

The first release does not include automatic timed coalescing, DDL or migrations,
query sources, streaming formats, Arrow or DataFrame APIs, schema inference or
coercion, distributed-table routing, plugin-generated deduplication tokens,
mutations, or upserts.

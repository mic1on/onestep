---
title: ClickHouse | Broker
outline: deep
---

# ClickHouse

`onestep-clickhouse` provides an async ClickHouse table output Sink. Each batch insertion waits for server acknowledgment.

## Installation

```bash
pip install onestep-clickhouse
```

Requires Python 3.9+ and `onestep>=1.9.0`.

## Python Usage

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

The connector lazily creates an async client on first insert. Connector close releases its own created client; injected clients have their lifecycle managed by the caller. `close()` is idempotent.

## YAML Configuration

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

Use `onestep check --strict worker.yaml` for static validation. `dsn` and `client_options` are sensitive metadata and do not appear in topology descriptions.

## Rows and Columns

Each `send()` accepts a mapping or a non-empty sequence of mappings. Strings, empty sequences, mixed types, or non-mapping items are rejected before the first network call.

When `columns` is configured, each row must contain exactly those keys, values arranged in configured order. When `columns` is omitted, the insertion order of the first mapping fixes the column order for that logical send; subsequent mappings must have an identical key set. The plugin does not infer database schemas or convert types.

`batch_size` defaults to 1000 rows. Larger logical batches are split into chunks and inserted sequentially, each awaited. The Sink has no timer, hidden flush queue, or cross-send batching; concurrency is controlled via task concurrency and the ClickHouse client connection pool.

## Acknowledged Async Insertions

A successful send means each chunk received an acknowledged server response. `async_insert: 0` is the only accepted non-declared wait mode. When async insert is enabled, `wait_for_async_insert: 1` must also be set:

```yaml
settings:
  async_insert: 1
  wait_for_async_insert: 1
```

## Delivery and Duplicate Semantics

Sink waits for each ClickHouse insert chunk and its acknowledgment. Crashes between ClickHouse chunk acknowledgment and onestep source acknowledgment may produce duplicate rows. Subsequent chunk failures are reported as UNCERTAIN because already-acknowledged chunks cannot be rolled back. For idempotency, design tables with stable event keys and a deduplicating engine like `ReplacingMergeTree`.

Multi-sink fan-out is not transactional. When a later sink fails, data already successfully written to ClickHouse is not rolled back. Explicit retries may duplicate already committed rows or chunks.

For example, a data table can retain stable event keys and a version field for eventual replacement:

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

This is a deployment recommendation only. The plugin does not execute DDL or generate dedup markers.

## Not Supported in Initial Release

The first release does not include: scheduled merges, DDL/migration, query Source, streaming formats, Arrow or DataFrame API, schema inference and conversion, distributed table routing, plugin-generated dedup markers, mutations, or upsert.

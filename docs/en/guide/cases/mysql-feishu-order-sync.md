---
title: 'Case: MySQL Order Stream to Feishu Bitable | Guide'
outline: deep
---

# Case: MySQL Order Stream to Feishu Bitable

This case incrementally writes **immutable order records** from a MySQL view into a Feishu Bitable. It's suitable for append-only sources where the target deduplicates by business key: process crashes or network timeouts may cause replay, but replay must not create duplicate Feishu records.

```text
view_order_sync
  └─ mysql_incremental: (orderCreateTime, orderKey)
       └─ handler:to_feishu_fields
            └─ feishu_bitable_table_sink: insert / 订单编号
```

## Goals & Boundaries

- The source sorts stably by `(orderCreateTime, orderKey)` composite cursor. `orderKey` is globally unique and unchanging as the final tie-breaker.
- Each source record is only acknowledged and the MySQL cursor advanced after Feishu confirms "already exists" or "created".
- Semantics are at-least-once: if the process crashes after a successful Feishu write but before the cursor is committed, the record will replay. The target deduplicates on `订单编号`, so replay won't create duplicate records with the same ID.
- `insert_key_index` is only suitable for immutable Insert streams; it does not provide updates, deletes, CDC, multi-writer exactly-once, or a persistent idempotency ledger.

## Prerequisites

Install these versions or later:

```bash
pip install 'onestep-mysql>=0.5.1' 'onestep-feishu-bitable>=0.4.0'
```

`onestep-mysql 0.5.1` persists and restores MySQL `DATETIME` cursor components with microsecond precision preserved. `onestep-feishu-bitable 0.4.0` provides bounded startup key indexing and indeterminate batch write recovery.

Before starting, verify:

1. `view_order_sync` outputs `orderCreateTime`, `orderKey`, and all business fields required by the handler; `orderKey` is unique and stably sorted within each `orderCreateTime`.
2. The underlying view query has an index supporting `(orderCreateTime, orderKey)`. Do not apply expressions to cursor columns that would break indexing or sorting.
3. The target Feishu table has a text field `订单编号`, with each order record having a stable and unique value.
4. There is exactly **one active writer instance** for the target `(app_token, table_id)` at all times. Once `insert_key_index` is enabled, a second worker or manual parallel write introduces race conditions after index startup.
5. The runtime account can create the `onestep_cursor` table. If DDL permission is unavailable, create the table first and set `auto_create` to `false`.

## Full YAML

Save as `worker.yaml`. Credentials are passed only through environment variables; `state_key` is the stable identity for sync progress—do not change it arbitrarily when publishing or renaming resources.

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: feishu-order-sync
  shutdown_timeout_s: 120
  strict_env: true
  config:
    environment: "${FEISHU_ORDER_ENV:-default}"
  logging:
    level: "${FEISHU_ORDER_LOG_LEVEL:-INFO}"

resources:
  mysql_main:
    type: mysql
    dsn: "${FEISHU_ORDER_MYSQL_DSN}"

  # Persistent incremental cursor; auto-creates table on first run.
  order_cursors:
    type: mysql_cursor_store
    connector: mysql_main
    table: onestep_cursor
    auto_create: true

  order_source:
    type: mysql_incremental
    connector: mysql_main
    table: view_order_sync
    key: orderKey
    cursor: [orderCreateTime, orderKey]
    batch_size: 1000
    poll_interval_s: 1
    state: order_cursors
    # Renaming this is treated as a completely new sync progress.
    state_key: feishu-order-sync-v1

  feishu:
    type: feishu_bitable
    app_id: "${FEISHU_ORDER_FEISHU_APP_ID}"
    app_secret: "${FEISHU_ORDER_FEISHU_APP_SECRET}"

  order_table:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${FEISHU_ORDER_FEISHU_APP_TOKEN}"
    table_id: "${FEISHU_ORDER_FEISHU_TABLE_ID}"
    mode: insert
    match_fields: [订单编号]
    user_id_type: user_id
    batch_size: 100
    flush_interval_s: 1

    # Reads existing `订单编号` keys on startup; normal incremental path
    # no longer searches Feishu record-by-record.
    insert_key_index: true
    insert_index_page_size: 500
    insert_index_max_pages: 200
    ambiguous_write_max_rounds: 3

tasks:
  - name: sync_orders
    description: Sync immutable order rows from MySQL to Feishu Bitable
    source: order_source
    emit: order_table
    concurrency: 100
    timeout_s: 120
    retry:
      type: max_attempts
      max_attempts: 5
      delay_s: 5
    handler: handler:to_feishu_fields
    config:
      batch_size: 100
```

`mysql_incremental.batch_size: 1000` is the maximum rows pulled in one SQL query;
`order_table.batch_size: 100` is the Feishu batch write boundary; `concurrency: 100` is
the runtime limit on in-flight Deliveries. None of these are interchangeable; `tasks[].config.batch_size`
is passed to the handler as `ctx.task_config` only, and does not change MySQL or Feishu batch sizes.

## Handler Contract

YAML only connects resources and does not carry field transformations. `handler:to_feishu_fields` must be a callable importable from the current Python runtime environment; it receives `(ctx, row)`, where `row` is a dictionary row from `view_order_sync`, and returns a field dictionary that can be written directly to Feishu.

The minimum contract is to return `订单编号`, whose value must stably correspond to this order record:

```python
async def to_feishu_fields(ctx, row):
    # `订单编号` and other Feishu fields are mapped by the business project;
    # do not put this logic into YAML.
    return {
        "订单编号": ...,
        # other target fields
    }
```

The mapping function should not alter the source data meaning of `orderCreateTime` or `orderKey`. If Feishu person fields use `user_id`, the returned structure must also match that field type; see
[Feishu Bitable Field Mapping](/broker/feishu-bitable#field-mapping) for details.

## First Deployment

1. First install both plugins and the business handler package, then run:

   ```bash
   onestep check --strict worker.yaml
   ```

2. Fully stop any old workers; ensure only one instance writes to the target Feishu table.
3. Start the worker. It will paginate and load existing `订单编号` keys from Feishu. If the target has more than `500 × 200 = 100,000` records, it will safely fail to start. Adjust `insert_index_max_pages` based on actual table size; do not accept a truncated index.
4. Observe startup completion and the first round of writes. The normal incremental path does not search Feishu record-by-record; batch writes are triggered at 100 records or after a 1-second flush interval.

## Running & Recovery

### Cursor & Retry

Successful records can complete out of order, but the persistent cursor only advances through consecutive successful prefixes. When a record enters retry, subsequent SQL fetches pause until the gap succeeds or exhausts `max_attempts`; after exhaustion, the Source stops before the failed row. Records that were not persisted will replay on restart.

`onestep-mysql 0.5.1+` stores `DATETIME` cursor components as typed ISO-8601 JSON, restoring them to the original `datetime` object on startup before participating in MySQL keyset queries. Therefore there's no need to convert `orderCreateTime` to a string or migrate the cursor table.

If an old `0.5.0` worker reports the following error after `mysql incremental cursor commit`:

```text
TypeError: Object of type datetime is not JSON serializable
```

Stop the old worker, upgrade to `onestep-mysql>=0.5.1`, and restart with the same `state_key`.
**Do not manually advance `onestep_cursor` past the failing row**: Feishu may have accepted some records, and unconfirmed records cannot be skipped. Replay will recognize them as already existing via the `订单编号` key index.

### Observation Points

With INFO logging enabled, you can inspect structured events:

| Event | Key Fields | Purpose |
|---|---|---|
| `mysql_incremental_fetch` | `row_count`, `pending_cursor_rows` | View source pull volume and uncommitted backlog |
| `mysql_incremental_retry` | `retry_count`, `attempt` | View retries on the same logical row |
| `mysql_incremental_cursor_commit` | `outcome`, `coalesced_ack_count` | Confirm consecutive prefix persistence |
| `feishu_insert_batch_write` | `batch_size`, `outcome` | View Feishu batch write results |
| `feishu_insert_retry` | `recovery_round`, `unresolved_count` | View indeterminate batch write precise-search recovery |

Do not output DSN, `app_secret`, or `app_token` in logs or case configuration.

## Parameter Trade-offs

| Parameter | This Case | Trade-off |
|---|---:|---|
| MySQL `batch_size` | 1000 | Reduces polling overhead; still constrained by available concurrency limits |
| Feishu `batch_size` | 100 | Write acknowledgment boundary; adjust per Feishu API and latency requirements |
| `flush_interval_s` | 1 | Low-traffic wait of about one second before sending partial batches |
| `concurrency` | 100 | Limits in-flight Deliveries; does not equal 100 concurrent MySQL queries |
| `insert_index_max_pages` | 200 | Bounded protection for startup scan; must cover actual target table pages |
| `max_attempts` | 5 | Temporary failures can retry; permanent data issues block before the failing row for easy debugging |

## Related Documentation

- [MySQL: Reliable Persistent Cursor & Retry](/broker/mysql#reliable-persistent-cursor-and-retry)
- [Feishu Bitable: High-Throughput Insert Incremental Sync](/broker/feishu-bitable#high-throughput-insert-incremental-sync)
- [YAML Task Definition](/yaml-task-definition)

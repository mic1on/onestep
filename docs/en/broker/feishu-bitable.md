---
title: Feishu Bitable | Broker
outline: deep
---

# Feishu Bitable

`onestep-feishu-bitable` enables using Feishu (Lark) Bitable as an incremental Source or table output Sink. It is suitable for MySQL to Bitable sync and incremental replication between Bitables.

## Installation

```bash
pip install onestep-feishu-bitable
```

After installation, the plugin automatically registers YAML resource types via the `onestep.resources` entry point.

## Python Usage

```python
from onestep import OneStepApp
from onestep_feishu_bitable import FeishuBitableConnector

app = OneStepApp("feishu-sync")
feishu = FeishuBitableConnector(
    app_id="cli_xxx",
    app_secret="secret",
)

source = feishu.incremental(
    app_token="bascn_source",
    table_id="tbl_source",
    cursor_field="updated_at",
    user_id_type="user_id",
    batch_size=100,
    fallback_scan_page_limit=100,
)

sink = feishu.table_sink(
    app_token="bascn_target",
    table_id="tbl_target",
    mode="upsert",
    match_fields=["order_id"],
    user_id_type="user_id",
)


@app.task(source=source, emit=sink, concurrency=4)
async def copy_row(ctx, payload):
    fields = payload["fields"]
    return {
        "order_id": fields["order_id"],
        "title": fields.get("title"),
        "updated_at": fields.get("updated_at"),
    }
```

The incremental Source output has the shape:

```python
{
    "record_id": "recxxxx",
    "fields": {"order_id": "A001", "updated_at": "2026-06-08T10:00:00+08:00"},
}
```

The table Sink accepts direct field mappings as well as `{"fields": ...}` wrapped payloads. Field names are passed to Feishu as-is; you can use Chinese display names from the Bitable.

## YAML Configuration

```yaml
resources:
  feishu:
    type: feishu_bitable
    app_id: "${FEISHU_APP_ID}"
    app_secret: "${FEISHU_APP_SECRET}"

  source_orders:
    type: feishu_bitable_incremental
    connector: feishu
    app_token: "${SOURCE_FEISHU_APP_TOKEN}"
    table_id: "${SOURCE_FEISHU_TABLE_ID}"
    cursor_field: updated_at
    user_id_type: user_id
    batch_size: 100
    fallback_scan_page_limit: 100

  target_orders:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${TARGET_FEISHU_APP_TOKEN}"
    table_id: "${TARGET_FEISHU_TABLE_ID}"
    mode: upsert
    match_fields: [order_id]
    user_id_type: user_id

tasks:
  - name: sync_orders
    source: source_orders
    emit: target_orders
    handler:
      ref: worker.tasks.orders:map_order_fields
    concurrency: 4
```

## Field Conversion

Feishu text fields sometimes return rich text arrays or objects. Before writing to a plain text field, use the plugin's helper functions in the handler:

```python
from onestep_feishu_bitable import feishu_bitable_text, feishu_bitable_user


async def map_order_fields(ctx, payload):
    fields = payload["fields"]
    return {
        "order_id": feishu_bitable_text(fields.get("order_id")),
        "title": feishu_bitable_text(fields.get("title")),
        "owner": feishu_bitable_user(fields.get("owner_id")),
    }
```

`feishu_bitable_user("u_xxx")` returns the `[{"id": "u_xxx"}]` structure required by Feishu person fields. `user_id_type` must match the type of user ID you provide.

## Relation Fields

Table Sink can use `relations` to resolve business keys into Feishu relation `record_id`s. For example, an enterprise table uses "enterprise_name" as the unique identifier, and a project table's "related_enterprises" field may relate to multiple enterprises:

```yaml
resources:
  projects:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${FEISHU_APP_TOKEN}"
    table_id: "${PROJECT_TABLE_ID}"
    mode: upsert
    match_fields: [project_id]
    relations:
      related_enterprises:
        from: enterprise_name
        table_id: "${ENTERPRISE_TABLE_ID}"
        key: enterprise_name
        on_missing: create
        create_fields:
          data_status: pending
```

The handler does not need to query the enterprise table; continue returning business values:

```python
async def map_project(ctx, payload):
    return {
        "project_id": payload["project_id"],
        "project_name": payload["project_name"],
        "enterprise_name": ["Enterprise A", "Enterprise B", "Enterprise C"],
    }
```

The Sink queries each enterprise name and converts the project request to:

```python
{
    "project_id": "P-001",
    "project_name": "Joint Construction Project",
    "related_enterprises": ["rec_a", "rec_b", "rec_c"],
}
```

Input can also be a single string. A string is parsed as one ID; a list or tuple resolves each entry; empty values are ignored; duplicates are deduplicated preserving first-occurrence order. When `from` is omitted, the business value is read from the relation field itself.

`on_missing` supports three strategies:

| Strategy | Behavior When Related Record Is Not Found |
|----------|-------------------------------------------|
| `error` | Default; any non-empty value not found fails the entire target record |
| `empty` | Skip unfound values; write `[]` when all are unfound; clearing existing relations on updates |
| `create` | Create missing records using `key` and `create_fields`, then write the new ID to the relation field |

`key` must remain business-unique in the related table. Multiple matches cause the Sink to fail, not randomly pick one. `create` avoids concurrent duplicate creation within the same Sink instance, but there is no global atomic dedup guarantee across multiple worker processes or deployment instances.

The related table defaults to the same `app_token` as the target table. For cross-Base relations, add `app_token` to the relation config:

```yaml
relations:
  related_enterprises:
    from: enterprise_name
    app_token: "${ENTERPRISE_FEISHU_APP_TOKEN}"
    table_id: "${ENTERPRISE_TABLE_ID}"
    key: enterprise_name
    on_missing: error
```

Feishu bidirectional relation reverse fields are maintained by the Feishu server based on field configuration; the plugin only writes the current target table's relation field.

### Relation Field Cache

Every relation resolution calls the Feishu records search API, which is rate-limited to **20 QPS**. During batch sync the same business keys recur batch after batch, so repeated searches can easily exhaust the quota and trigger `429`. Configuring `cache` on a relation field caches resolution results in the Sink instance's memory:

```yaml
relations:
  related_enterprise:   # stable master data, hot keys within a task -> preload at startup
    from: enterprise_name
    table_id: "${ENTERPRISE_TABLE_ID}"
    key: enterprise_name
    on_missing: create
    cache: eager
  responsible_dept:     # large key space, only a small slice touched per run -> cache on read
    from: dept_code
    table_id: "${DEPT_TABLE_ID}"
    key: dept_code
    on_missing: error
    cache: lazy
```

| Value | Semantics | Use case |
|---|---|---|
| `none` | Default; every unique business key searches on every resolution, identical to omitting `cache` | Related table changes frequently, consistency-sensitive, or a single run touches only a few records |
| `lazy` | Cache `{business key: record_id}` after a successful search; hits are used directly, misses search the source and backfill | General recommendation; large key space where a single run touches only a small slice, avoiding startup scan cost |
| `eager` | Page through the related table's `key` field into memory at `open()`; hits are used directly, misses follow `on_missing` (`empty` treats as absent and skips search, `error`/`create` still search the source) | Related table is stable master data with a bounded record count, and the same keys are referenced frequently during the run |

`cache` is configured **per relation field**; the three values can be mixed within a single Sink.

**The cache is only an acceleration layer; Feishu remains the source of truth**: cache hits are used optimistically, adopting the cached `record_id`; **a miss must search the source once**, and is never mistaken for "does not exist in Feishu". So even if the related table gains records after startup, `on_missing: create` still searches first to confirm and never creates duplicate master data. The sole exception is `eager` + `on_missing: empty`: a startup-snapshot miss is treated as genuinely absent, skipping the search, with neither an error nor a backfill.

`eager` startup-scan constraints:

- Reuses the sink-level `insert_index_page_size` (default `500`) and `insert_index_max_pages` (default `200`), i.e. a single related table scans at most **100K** records, without introducing a second set of config keys.
- The scan requests only the `key` field; records with a missing or empty requested field are skipped, and duplicate keys have later values overwrite earlier ones — all recorded in the structured log `feishu_relation_cache_scan`.
- `key` can be a text field (type=1): the rich-text segments Feishu returns (e.g. `[{"text": "某公司", "type": "text"}]`) are automatically flattened to plain text before entering the cache, matching the rich-text business value on the `from` side. Other composite field types (person, attachment, location, etc.) are not guaranteed to flatten correctly; prefer fields that return plain values (number/formula/single-select, etc.).
- If the scan ends with an empty cache while the source table did have skipped records (`scan_keys == 0` and `missing_key_records > 0`), an extra WARNING log `warn_empty` is emitted to flag that the key field may be unparseable, preventing silent relation failure.
- On reaching the page limit while Feishu still has more records, or when the pagination token stops advancing, `open()` fails with a permanent error rather than starting with a truncated cache.
- `eager` assumes the related table has **no concurrent writes that conflict with this task** during the run (the same single-writer premise as `insert_key_index`). New records do not cause data errors, only an extra one-time search cost for that key; deleting or modifying a key causes a dangling/stale cache entry.

Operations note: **the cache is unaware of deletions and key modifications in the related table, is not persisted, and is not shared across processes**. A dangling `record_id` is rejected by Feishu on write (`RecordIdNotFound` / `LinkFieldConvFail`); **restarting the task rebuilds the cache** and converges.

## Important Parameters

| Parameter | Description |
|-----------|-------------|
| `cursor_field` | High-water mark field for incremental reads |
| `match_fields` | Business-unique fields for matching target records on upsert |
| `batch_size` | Maximum records fetched per pull |
| `fallback_scan_page_limit` | Max pages for local fallback scan when Feishu rejects cursor sorting; default `100` |
| `user_id_type` | ID type for person fields, e.g., `open_id`, `union_id`, `user_id` |
| `relations` | Field-level mapping from business keys to relation record IDs |
| `relations.*.cache` | Relation resolution cache strategy: `none` (default) / `lazy` / `eager`, see "Relation Field Cache" |

`fallback_scan_page_limit` is a guard threshold. Only increase this value when table size and API call quotas allow fallback scanning.

## High-Throughput Insert Incremental Sync

For immutable operation records, enable `insert_key_index` for `insert` Sink: the Sink reads
only one `match_fields` into an in-memory set during startup, then normal processing skips
per-record Search calls. For a target table with 50K records and page size 500, startup scans
about 100 pages. If the scan exceeds `insert_index_max_pages`, startup fails directly instead
of using a truncated index.

```yaml
order_table:
  type: feishu_bitable_table_sink
  connector: feishu
  app_token: "${FEISHU_APP_TOKEN}"
  table_id: "${FEISHU_TABLE_ID}"
  mode: insert
  match_fields: [order_id]
  batch_size: 100
  flush_interval_s: 1
  insert_key_index: true
  insert_index_page_size: 500
  insert_index_max_pages: 200
  ambiguous_write_max_rounds: 3
```

This mode only supports one match field and cannot be combined with `relations`. Each
`send()` only returns after confirming records either already exist or are successfully
created in the batch, so upstream Delivery is not prematurely acknowledged while data
remains in a private buffer. Timeouts, disconnections, or incomplete responses first
precisely query the affected batch, then only create explicitly missing keys; a failed
query is never treated as "does not exist".

The startup index is only an in-memory snapshot at `open()` time, so a key that hits the snapshot is first re-queried precisely to confirm the record still exists before being skipped. Otherwise, if the target row is deleted after startup, the record would be silently dropped while the upstream still acknowledges and advances the cursor, causing permanent data loss. The cost is: **one extra query per re-sent existing key**; keys written successfully by this instance need no query.

If the target table is append-only for the whole run (rows are never deleted or moved), set
`insert_index_skip_verification: true` to restore the "zero-query" fast path. This is the highest-throughput mode, and it also accepts the risk of "silently skipping existing keys whose rows have disappeared".

`close_drain_max_rounds` (default `1000`) caps the drain during close, throwing `ConnectorOperationError` instead of looping forever; calling `send()` after `close()` immediately raises a permanent `ConnectorOperationError` rather than silently writing to a buffer that will never flush again.

The in-memory index requires only one active write instance for the same
`(app_token, table_id)`. Manual additions or a second worker will cause startup races.
This mode does not save record IDs, nor does it provide persistent idempotency ledger,
updates, deletes, CDC, or multi-writer exactly-once guarantees.

For combining this mode with MySQL composite cursors, retries, and safe recovery, see
[User Case: MySQL to Feishu Bitable Order Sync](/en/guide/cases/mysql-feishu-order-sync).

## Next Steps

- [YAML Task Definition](/en/yaml-task-definition) - View plugin resource registration and strict validation
- [MySQL](/en/broker/mysql) - Incremental sync from database to Bitable
- [HTTP Sink](/en/broker/http) - Connect to standard HTTP APIs

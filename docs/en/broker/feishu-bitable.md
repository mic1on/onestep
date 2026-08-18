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

## Important Parameters

| Parameter | Description |
|-----------|-------------|
| `cursor_field` | High-water mark field for incremental reads |
| `match_fields` | Business-unique fields for matching target records on upsert |
| `batch_size` | Maximum records fetched per pull |
| `fallback_scan_page_limit` | Max pages for local fallback scan when Feishu rejects cursor sorting; default `100` |
| `user_id_type` | ID type for person fields, e.g., `open_id`, `union_id`, `user_id` |
| `relations` | Field-level mapping from business keys to relation record IDs |

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

The in-memory index requires only one active write instance for the same
`(app_token, table_id)`. Manual additions or a second worker will cause startup races.
This mode does not save record IDs, nor does it provide persistent idempotency ledger,
updates, deletes, CDC, or multi-writer exactly-once guarantees.

For combining this mode with MySQL composite cursors, retries, and safe recovery, see
[User Case: MySQL to Feishu Bitable Order Sync](/guide/cases/mysql-feishu-order-sync).

## Next Steps

- [YAML Task Definition](/yaml-task-definition) - View plugin resource registration and strict validation
- [MySQL](/broker/mysql) - Incremental sync from database to Bitable
- [HTTP Sink](/broker/http) - Connect to standard HTTP APIs

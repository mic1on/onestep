---
title: MySQL | Broker
outline: deep
---

# MySQL

MySQL Connector provides three modes:
- **Table Queue**: Use a database table as a task queue
- **Incremental Sync**: Logstash-style sync based on `(updated_at, id)`
- **Table Sink**: Write results to a database table

## Installation

```bash
pip install 'onestep-sql[mysql]'
# or use the core extra
# pip install 'onestep[mysql]'
```

> `onestep-sql` is the canonical distribution package for MySQL and PostgreSQL (issue #133). The legacy `pip install onestep-mysql` still works as a forwarding shim, but new deployments should use `onestep-sql[mysql]`. All YAML resource type names are unchanged.

## Table Queue

Use a database table as a task queue by updating status fields to "claim" tasks.

### Basic Usage

```python
from onestep import OneStepApp
from onestep_mysql import MySQLConnector

app = OneStepApp("orders")

# Create connection
db = MySQLConnector("mysql+pymysql://root:root@localhost:3306/app")

# Create table queue Source
source = db.table_queue(
    table="orders",
    key="id",
    where="status = 0",           # Query condition: pending
    claim={"status": 9},          # Set on claim: processing
    ack={"status": 1},            # Set on success: completed
    nack={"status": 0},           # Set on failure: pending (retryable)
    batch_size=100,               # Batch claim size
)

# Create table sink
sink = db.table_sink(
    table="processed_orders",
    mode="upsert",                # Insert or update
    keys=("id",),                 # Unique keys
)


@app.task(source=source, emit=sink, concurrency=16)
async def process_order(ctx, row):
    return {
        "id": row["id"],
        "payload": row["payload"],
        "status": "done"
    }


if __name__ == "__main__":
    app.run()
```

### Workflow

1. Query records with `status = 0`
2. Batch update `status = 9` (claim)
3. Execute task
4. On success: update `status = 1`
5. On failure: update `status = 0` (retryable)

### Status Management

```python
# Status flow
where="status = 'pending'"    # Pending
claim={"status": "processing"} # Processing
ack={"status": "completed"}   # Completed
nack={"status": "failed"}     # Failed
```

## Incremental Sync

Incremental data sync based on `(updated_at, id)`, suitable for data warehouse scenarios.

### Basic Usage

```python
from onestep import MemoryQueue, OneStepApp
from onestep_mysql import MySQLConnector

app = OneStepApp("sync-users")
db = MySQLConnector("mysql+pymysql://root:root@localhost:3306/app")

# Cursor store (persistent position)
cursor_store = db.cursor_store(table="onestep_cursor")

# Incremental sync Source
source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),   # Cursor fields
    where="deleted = 0",           # Filter condition
    batch_size=1000,               # Batch size
    state=cursor_store,            # State store
)

# Output to memory queue
out = MemoryQueue("dw")


@app.task(source=source, emit=out, concurrency=1)
async def sync_user(ctx, row):
    return {
        "id": row["id"],
        "name": row["name"],
        "updated_at": row["updated_at"]
    }
```

### How It Works

1. Read last position from `cursor_store`
2. Query `updated_at > last_updated OR (updated_at = last_updated AND id > last_id)`
3. Process data
4. Update position in `cursor_store`

### Cursor Store

```python
# Database store (recommended for production)
cursor_store = db.cursor_store(table="sync_cursor")

# Or state store
state_store = db.state_store(table="onestep_state")
```

## Table Sink

Write processing results to a database table.

### Upsert Mode

```python
sink = db.table_sink(
    table="results",
    mode="upsert",
    keys=("id",),  # Unique keys: update if exists, insert if not
)

@app.task(source=..., emit=sink)
async def process(ctx, item):
    return {"id": item["id"], "data": item["data"]}
```

### Insert Mode

```python
sink = db.table_sink(
    table="logs",
    mode="insert",  # Insert only
)
```

> Note: `upsert` generates `INSERT ... ON DUPLICATE KEY UPDATE`. Even when the key already
> exists and the update branch is taken, MySQL still applies constraint checks to the INSERT
> part. When the target table has `NOT NULL` columns without default values and the payload
> omits those columns, it produces a `Field 'xxx' doesn't have a default value` warning
> (the update itself still succeeds). Use `mode="update"` when only existing rows need updating.

### Update Mode

Updates only existing rows, never inserts new ones (`UPDATE ... WHERE`):

```python
sink = db.table_sink(
    table="bidding",
    mode="update",
    keys=("id",),
    update_columns=("deadline", "tender_deadline"),
)
```

- Suitable for scenarios where "the target row is created by another process and this task
  only backfills specific fields."
- Skips non-matching rows with an INFO log (not an error); MySQL also treats no-change
  updates as 0 affected rows.
- Does not generate `INSERT` statements, so `NOT NULL` columns without defaults do not
  trigger warnings, and there is no accidental-insert risk.

### Update Control (Upsert / Update Behavior)

In `upsert` and `update` modes, use `update_columns` and `update_expr` to precisely
control which columns are written:

```python
sink = db.table_sink(
    table="results",
    mode="upsert",
    keys=("id",),
    update_columns=("data",),          # Only overwrite these columns on conflict
    update_expr={"updated_at": "NOW(6)"},  # Raw SQL expressions on conflict
)
```

- `update_columns`: Whitelist of columns allowed to be overwritten on conflict;
  defaults to all payload columns except `keys`. An empty tuple `()` means no
  payload columns are updated on conflict, only `update_expr` is applied.
- `update_expr`: Mapping from column name to raw SQL expression executed on
  conflict (e.g., `updated_at=NOW(6)`).
- Both apply to `upsert` and `update` modes; configuration is invalid when
  `update_columns` is empty and there is no `update_expr`.

### JSON Serialization Control

List/dict values in payloads are handled automatically based on the target column
type (`serialize_json="auto"`): written as-is when the column type is JSON,
otherwise serialized as a JSON string:

```python
sink = db.table_sink(
    table="results",
    mode="insert",
    serialize_json="always",  # Always serialize as JSON string
)
```

`serialize_json` options: `auto` (default), `always` (always serialize to string),
`never` (never serialize).

### Per-Column Write Policies (null protection)

`update_columns` entries can be plain column names (unconditional overwrite) or
`{name, policy}` mappings that declare how payload values merge with existing
stored values per column. Three policies:

| policy | Behavior | Generated SQL |
|---|---|---|
| `overwrite` (default) | Unconditionally overwrite with payload value; payload `null` writes `NULL` | `SET col = :val` |
| `skip_null` | Skip the column when payload value is null, preserve the stored value | `null` → column removed from `SET` |
| `backfill` | Only write when the stored value is currently `NULL`; preserve non-null stored value | `SET col = COALESCE(col, :val)` |

```yaml
rows_sink:
  type: mysql_table_sink
  connector: downstream_mysql
  table: bidding
  mode: update
  keys: [id]
  update_columns:
    - deadline              # unconditional overwrite
    - tender_deadline       # unconditional overwrite
    - name: tenderee
      policy: skip_null     # payload null won't clear existing value
    - name: publish_date
      policy: backfill      # only fill null, don't overwrite existing
```

Python API accepts mixed entries:

```python
sink = db.table_sink(
    table="bidding",
    mode="update",
    keys=("id",),
    update_columns=(
        "deadline",
        {"name": "tenderee", "policy": "skip_null"},
    ),
)
```

Notes:

- Policies apply to both `update` and `upsert` modes (the `ON DUPLICATE KEY UPDATE`
  clause uses the same rules).
- When `skip_null` filtering leaves the entire `SET` clause empty, that row is
  skipped with an INFO log (not an error).
- Policy columns cannot be in `keys`, nor can they be configured alongside
  `update_expr` for the same column (construction-time error).

## State Store

### State Store

Key-value storage for task state:

```python
state = db.state_store(table="onestep_state")

# Use in tasks
@app.task(source=...)
async def process(ctx, item):
    count = await ctx.state.get("processed_count", 0)
    await ctx.state.set("processed_count", count + 1)
```

### Cursor Store

Cursor store for incremental sync position:

```python
cursor = db.cursor_store(table="sync_cursor")

source = db.incremental(
    table="orders",
    key="id",
    cursor=("updated_at", "id"),
    state=cursor,
)
```

## YAML Configuration

```yaml
resources:
  db:
    type: mysql
    dsn: "mysql+pymysql://root:root@localhost:3306/app"
  
  order_queue:
    type: mysql_table_queue
    connector: db
    table: "orders"
    key: "id"
    where: "status = 0"
    claim:
      status: 9
    ack:
      status: 1
    batch_size: 100
  
  results:
    type: mysql_table_sink
    connector: db
    table: "results"
    mode: "upsert"
    keys:
      - "id"
    update_columns:
      - "data"
    update_expr:
      updated_at: "NOW(6)"
    serialize_json: "auto"
  
  cursor:
    type: mysql_cursor_store
    connector: db
    table: "sync_cursor"

tasks:
  - name: process_orders
    source: order_queue
    emit: results
    concurrency: 16
```

## Best Practices

### 1. Index Optimization

```sql
-- Table queue: ensure query conditions have an index
CREATE INDEX idx_status ON orders(status);

-- Incremental sync: ensure cursor fields have an index
CREATE INDEX idx_cursor ON users(updated_at, id);
```

### 2. Batch Size

```python
# Small batch: low latency
batch_size=10

# Large batch: high throughput
batch_size=1000
```

### 3. Concurrency Control

```python
# Table queue: high concurrency (row-level locks)
@app.task(source=source, concurrency=16)

# Incremental sync can be processed concurrently; Runner still calls fetch(limit) once per round
# concurrency limits in-flight Delivery, not 100 concurrent SELECT queries
@app.task(source=incremental, concurrency=100)
```

### 4. Connection Pool

```python
# URL parameters for pool configuration
db = MySQLConnector(
    "mysql+pymysql://user:pass@host/db"
    "?pool_size=10"
    "&max_overflow=20"
    "&pool_recycle=3600"
)
```

### 5. Reliable Persistent Cursor with Retry

Production incremental sync should explicitly bind a `mysql_cursor_store` and stable `state_key`. Successful records may complete out of order, but the persistent cursor only advances to the continuous success prefix; acknowledgments from the same batch are merged into a single state write. Failed retries re-deliver the same logical row and increment `Envelope.attempts`. During gap retries, subsequent SQL queries are not issued. After reaching the task's `max_attempts`, the Source stops before the failed row. Process restart recovers from the persisted cursor; unacknowledged rows are replayed.

Starting from `onestep-mysql 0.5.1`, `mysql_cursor_store` persists MySQL `DATETIME` cursor components: they are saved as type-tagged ISO-8601 JSON and restored as the original `datetime` (with microseconds preserved) for keyset queries on restart. Existing plain JSON cursors remain compatible; upgrading from `0.5.0` requires no cursor table migration, and you should not manually advance a cursor that has not been acknowledged due to a commit failure.

```yaml
mysql_cursors:
  type: mysql_cursor_store
  connector: mysql_source
  table: onestep_cursor
  auto_create: true

order_source:
  type: mysql_incremental
  connector: mysql_source
  table: view_order_sync
  key: orderKey
  cursor: [orderCreateTime, orderKey]
  state: mysql_cursors
  state_key: feishu-order-sync-v1
```

For complete production parameters, Feishu Insert key index, handler contract, and failure recovery flow, see
[User Case: MySQL to Feishu Bitable Order Sync](/en/guide/cases/mysql-feishu-order-sync).

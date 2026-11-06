---
title: SQL (MySQL / PostgreSQL) | Broker
outline: deep
---

# SQL <Badge type="tip" text="MySQL" /> <Badge type="tip" text="PostgreSQL" />

`onestep-sql` is the canonical distribution package for MySQL and PostgreSQL (issue #133), providing a unified Python API and YAML resource model for both backends with the same Source/Sink abstractions: table queues, incremental polling, table sinks, and cursor/state stores. Differences are limited to SQL dialect and driver parameters.

```bash
# MySQL
pip install 'onestep-sql[mysql]'
# PostgreSQL
pip install 'onestep-sql[postgres]'
# Both
pip install 'onestep-sql[mysql,postgres]'
```

::: info
The legacy `onestep-mysql` and `onestep-postgres` packages now serve as forwarding shims for compatibility. New deployments should use `onestep-sql` directly. Python import paths `from onestep_mysql import ...` / `from onestep_postgres import ...` still work.
:::

Beyond table queues, incremental sync, and table sinks, `onestep-sql` also provides tracked execution, using the database as the store for long-running task submission, status, results, cancellation, and leases: see [MySQL Tracked Execution](/en/broker/mysql-execution) and [PostgreSQL Tracked Execution](/en/broker/postgres-execution).

## Table Queue

Use database row-level locks to claim tasks, turning a table into a durable queue.

::: code-group

```python [MySQL]
from onestep_sql.mysql import MySQLConnector

db = MySQLConnector("mysql+pymysql://user:pass@localhost/app")
source = db.table_queue(
    table="orders",
    key="id",
    where="status = 0",
    claim={"status": 9},
    ack={"status": 1},
    nack={"status": 0},
    batch_size=100,
)
```

```python [PostgreSQL]
from onestep_sql.postgres import PostgresConnector

db = PostgresConnector("postgresql+psycopg://user:pass@localhost/app")
source = db.table_queue(
    table="orders",
    key="id",
    where="status = 0",
    claim={"status": 9},
    ack={"status": 1},
    nack={"status": 0},
    batch_size=100,
)
```

:::

### Workflow

1. Query records with `status = 0`
2. Batch update `status = 9` (claim)
3. Execute the task
4. Success: update `status = 1`
5. Failure: update `status = 0` (retryable)

```python
# Status flow
where="status = 'pending'"      # pending
claim={"status": "processing"}  # processing
ack={"status": "completed"}     # completed
nack={"status": "failed"}       # failed
```

### Single-Transaction Completion: complete()

For a table-queue delivery, `update_current_row(values)` and `ack()` are two
separate transactions: if the process crashes between them, a row is left with
business columns written but no ack. Most `where` clauses mask this, but it is
one of the root causes of the at-least-once duplication window.

`complete(values)` merges the row write-back and the acknowledgement into a
**single database transaction**: it first `UPDATE`s the business columns and
then `UPDATE`s the ack column(s) in the same transaction, committing only when
both succeed.

```python
@app.task(source=source, emit=sink)
async def process_order(ctx, row):
    # Write business columns and ack completion (e.g. status = 1 / 'done')
    # in one transaction
    await ctx.complete({"score": row["id"] * 10})
    return {
        "id": row["id"],
        "status": "done"
    }
```

It can also be called directly on the delivery: `await delivery.complete(values)`.

#### Relationship to the Two-Step API

- **Purely additive**: `complete(values)` is a new API; the old
  `update_current_row(values)` + `ack()` two-step usage is unchanged.
- With `ack={}`, `complete(values)` is equivalent to
  `update_current_row(values)`: only the business-column `UPDATE` remains.
- When both `values` and `ack` are empty, nothing is written.

#### Atomicity and Idempotency

- **Atomicity**: if `complete()` fails midway (e.g. the second statement
  errors), the whole transaction rolls back — neither write takes effect, the
  row stays in the claim state, and the normal failure path (`nack` / retry)
  applies. A half-applied "business columns written, ack missing" row cannot
  occur.
- **Ack idempotency**: on success, `complete()` sets `_complete_ack_applied`
  on the delivery. The executor still calls `delivery.ack()` by convention
  after the handler returns; that call is then skipped instead of issuing a
  redundant `UPDATE`. (Even without the skip, re-writing the ack is
  idempotent — this just saves one write.)
- The envelope body only mirrors the business `values`; ack columns are queue
  bookkeeping and never leak into the body.
- Lint note: `onestep check` warns when `claim` is non-empty while `nack` is
  empty (issue #180) — failed rows would silently stick in the claim state;
  `complete()` does not change that semantics.

> The API is provided by the shared implementation in `onestep-sql`
> (`onestep_sql/_shared/table_queue.py`) and behaves identically across the
> MySQL, PostgreSQL, and SQLite backends.

## Incremental Sync

Time-based incremental data sync with `(updated_at, id)` cursors, suitable for data warehouse scenarios.

::: code-group

```python [MySQL]
from onestep_sql.mysql import MySQLConnector

db = MySQLConnector("mysql+pymysql://user:pass@localhost/app")
cursor = db.cursor_store(table="onestep_cursor")

source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    where="deleted = 0",
    batch_size=1000,
    state=cursor,
)
```

```python [PostgreSQL]
from onestep_sql.postgres import PostgresConnector

db = PostgresConnector("postgresql+psycopg://user:pass@localhost/app")
cursor = db.cursor_store(table="onestep_cursor")

source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    where="deleted = 0",
    batch_size=1000,
    state=cursor,
)
```

:::

### How It Works

1. Read last position from `cursor_store`
2. Query `updated_at > last_updated OR (updated_at = last_updated AND id > last_id)`
3. Process data
4. Update position in `cursor_store`

MySQL expands composite cursors into range conditions to avoid row-constructor inequalities re-scanning already-processed index prefixes in some execution plans. You still need a same-order index for the full cursor; the connector appends `key` automatically when not configured.

### Cursor Store

```python
# Database store (recommended for production)
cursor_store = db.cursor_store(table="sync_cursor")

# Or state store
state_store = db.state_store(table="onestep_state")
```

## Table Sink

Write processing results to database tables. Python API is identical across both backends.

### Upsert Mode

```python
sink = db.table_sink(
    table="results",
    mode="upsert",
    keys=("id",),
)
```

> Note: MySQL `upsert` generates `INSERT ... ON DUPLICATE KEY UPDATE`. Even when the key exists and the update branch is taken, MySQL still performs constraint checking on the INSERT part — if the target table has `NOT NULL` columns without defaults that the payload doesn't provide, a `Field 'xxx' doesn't have a default value` warning is emitted (the update itself still succeeds). Use `mode="update"` when only updating existing rows.

### Insert Mode

```python
sink = db.table_sink(
    table="logs",
    mode="insert",
)
```

### Update Mode

Only updates existing rows, never inserts (`UPDATE ... WHERE`):

```python
sink = db.table_sink(
    table="bidding",
    mode="update",
    keys=("id",),
    update_columns=("deadline", "tender_deadline"),
)
```

- Suitable when "target rows are created by another process and this task only backfills certain fields."
- Skips non-existent rows with an INFO log, no error.
- Generates no `INSERT` statement, so no risk of accidental new rows.

### Update Control (Upsert / Update Behavior)

```python
sink = db.table_sink(
    table="results",
    mode="upsert",
    keys=("id",),
    update_columns=("data",),
    update_expr={"updated_at": "NOW(6)"},
)
```

- `update_columns`: whitelist of writable columns; defaults to all payload columns except `keys`.
- `update_expr`: column → raw SQL expression mapping (e.g. `updated_at=NOW(6)`).
- Both only apply to `upsert` and `update` modes.

### Per-Column Write Policies (null protection)

`update_columns` entries can be column names (unconditional overwrite) or `{name, policy}` objects:

| policy | behavior | generated SQL |
|---|---|---|
| `overwrite` (default) | Unconditionally overwrite with payload value | `SET col = :val` |
| `skip_null` | Skip column when payload is `null`, preserving the DB value | `null` → column omitted from `SET` |
| `backfill` | Only write payload when DB value is `NULL` | `SET col = COALESCE(col, :val)` |

```yaml
results:
  type: mysql_table_sink       # or postgres_table_sink
  connector: db
  table: bidding
  mode: update
  keys: [id]
  update_columns:
    - deadline                # unconditional overwrite
    - name: tenderee
      policy: skip_null       # don't overwrite with null
    - name: publish_date
      policy: backfill        # only backfill nulls
```

Python also accepts mixed entries:

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
- Policies apply to both `update` and `upsert`.
- When `skip_null` makes the entire `SET` empty, the row is skipped with an INFO log.
- Policy columns cannot be `keys` columns or conflict with `update_expr`.

### JSON Serialization Control

```python
sink = db.table_sink(
    table="results",
    mode="insert",
    serialize_json="always",
)
```

Options: `auto` (default — JSON columns written as-is, others serialized to string), `always`, `never`.

## State Store

### State Store

Key-value store for task state:

```python
state = db.state_store(table="onestep_state")

@app.task(source=...)
async def process(ctx, item):
    count = await ctx.state.get("processed_count", 0)
    await ctx.state.set("processed_count", count + 1)
```

### Cursor Store

Cursor store for incremental sync positions:

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

MySQL and PostgreSQL use different resource type prefixes in YAML.

::: code-group

```yaml [MySQL]
resources:
  db:
    type: mysql
    dsn: "mysql+pymysql://root:root@localhost:3306/app"

  order_queue:
    type: mysql_table_queue
    connector: db
    table: orders
    key: id
    where: "status = 0"
    claim: {status: 9}
    ack: {status: 1}
    batch_size: 100

  results:
    type: mysql_table_sink
    connector: db
    table: results
    mode: upsert
    keys: [id]

  cursor:
    type: mysql_cursor_store
    connector: db
    table: sync_cursor

tasks:
  - name: process_orders
    source: order_queue
    emit: results
    concurrency: 16
```

```yaml [PostgreSQL]
resources:
  db:
    type: postgres
    dsn: "postgresql+psycopg://user:pass@localhost/app"

  order_queue:
    type: postgres_table_queue
    connector: db
    table: orders
    key: id
    where: "status = 0"
    claim: {status: 9}
    ack: {status: 1}
    batch_size: 100

  results:
    type: postgres_table_sink
    connector: db
    table: results
    mode: upsert
    keys: [id]

  cursor:
    type: postgres_cursor_store
    connector: db
    table: sync_cursor

tasks:
  - name: process_orders
    source: order_queue
    emit: results
    concurrency: 16
```

:::

## Best Practices

### 1. Index Optimization

```sql
-- Table queue: ensure query condition has an index
CREATE INDEX idx_status ON orders(status);

-- Incremental sync: ensure cursor fields have an index
CREATE INDEX idx_cursor ON users(updated_at, id);
```

### 2. Read Batch and Processing Concurrency

Default (`prefetch=False`): each SQL hit fetches at most `min(batch_size, current free concurrency slots)`. With `batch_size=500` and `concurrency=8`, at most 8 rows per query.

Starting with `onestep-sql 0.3.0`, enable bounded prefetch on incremental sources:

```python
source = db.incremental(
    table="users", key="id", cursor=("updated_at",),
    batch_size=100, prefetch=True, state=cursor_store,
)

@app.task(source=source, concurrency=8)
async def sync_user(ctx, row):
    ...
```

YAML: `prefetch: true` and `batch_size: 100`. SQL reads at most 100 rows per query, delivering only as many as free slots; the rest are buffered until consumed.

- Undelivered buffer caps at `batch_size` rows.
- Prefetch does not persist the cursor early. Retries take priority over buffered rows.
- Pause/exit waits for any in-flight SELECT to finish.
- Start with 50, 100, or 500 rows against fixed concurrency, then compare SQL counts, memory, and latency.

### 3. Concurrency Control

```python
# Table queue: high concurrency (row-level locks)
@app.task(source=source, concurrency=16)

# Incremental sync: can be processed concurrently
@app.task(source=incremental, concurrency=100)
```

### 4. Connection Pool

```python
db = MySQLConnector(
    "mysql+pymysql://user:pass@host/db"
    "?pool_size=10&max_overflow=20&pool_recycle=3600"
)
```

### 5. Reliable Persistent Cursor with Retry

Production incremental sync should explicitly bind a `*_cursor_store` with a stable `state_key`. The persistent cursor only advances to the contiguous success prefix; failed rows are re-delivered with incremented `Envelope.attempts`, and no subsequent SQL query is issued during the retry gap. Process restart recovers from the persisted cursor.

Since `onestep-sql 0.3.0`, MySQL `DATETIME` cursor components persist as type-tagged ISO-8601 JSON and restore to original `datetime` (with microseconds) on reload. Existing plain JSON cursors remain compatible.

### 6. MySQL Binlog CDC

MySQL supports binlog CDC mode:

```python
from onestep_sql.mysql import MySQLConnector

db = MySQLConnector("mysql+pymysql://user:pass@localhost/app")

source = db.binlog(
    server_id=100,
    schemas=("myapp",),
    tables=("orders",),
    events=("insert", "update", "delete"),
    batch_size=100,
    state=cursor_store,
)

@app.task(source=source, concurrency=1)
async def handle_row_change(ctx, row):
    # row.schema, row.table, row.event (insert/update/delete)
    # row.values = changed columns; update also has row.before_values
    ...
```

| Parameter | Default | Description |
|---|---|---|
| `server_id` | required | Unique consumer ID in MySQL replication protocol |
| `schemas` | `()` | Databases to monitor (empty = all) |
| `tables` | `()` | Tables to monitor (empty = all) |
| `events` | `("insert","update","delete")` | Event types to listen for |
| `batch_size` | `100` | Batch size per fetch |
| `poll_interval_s` | `1.0` | Poll interval when no events |
| `state` | in-memory | Persistent cursor position |
| `blocking` | `False` | Whether to block waiting for new events |

Install `onestep-sql[mysql]` which includes `mysql-replication`. Requires MySQL with binlog enabled and `ROW` format.

#### Acknowledgement and Recovery

`mysql_binlog` persists `{file, pos}` as the complete contiguous prefix of
binlog events. Even when rows of the same event are fetched in batches and
processed out of order, the cursor can only advance to the end of that event
after all of them complete. If the cursor store write fails or is cancelled,
the in-memory unfinished barrier is kept so settlement can be retried.

- `retry()` puts the record back into a local redelivery queue, keeps the
  cursor barrier, and increments `Envelope.attempts`. `MaxAttempts` can thus
  reach its terminal state; the retry delay only controls when the record is
  delivered again and never blocks the settlement call.
- `release_unstarted()` redelivers records that never started, without
  incrementing the attempt count.
- Terminal `fail()` settles the row. When a dead-letter output is configured,
  the runtime writes the dead letter successfully first and only then advances
  the cursor; without one, a terminal failure means the record is dropped.
  Tasks that must retain failed data should configure a dead-letter output.
- The redelivery queue and attempt counters live only in the current process.
  After a restart (or closing and reopening the source), events not fully
  acknowledged are replayed from the persisted cursor with attempt counts
  reset to zero — rows that already succeeded may execute again; handlers and
  sinks must be idempotent. Calling settlement methods repeatedly on a single
  delivery neither redelivers twice nor crosses over to a new delivery.

`blocking=True` waits when there are no records; internally the source uses
non-blocking binlog reads with async polling at `poll_interval_s` intervals,
so pausing or shutting down never waits for the next database change. If the
fetch is cancelled, reads that already started are retained by the source and
delivered once fetching resumes; on close it waits for the current read to
finish before closing the connection.

## PostgreSQL Tracked Long-Running Execution

`onestep-sql[postgres]` can use PostgreSQL as the single source of truth for task state, results, and leases. FastAPI uses `ExecutionClient` from core, and workers use `PostgresExecutionSource`:

```python
from onestep import ExecutionClient
from onestep_sql.postgres import PostgresExecutionBackend, PostgresExecutionSource

backend = PostgresExecutionBackend(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=True,
    reclaim_batch_size=100,
)
client = ExecutionClient(backend, namespace="agent-api")
```

For the full workflow and state machine, see [PostgreSQL Tracked Execution](/en/broker/postgres-execution).

## Next Steps

- [PostgreSQL Tracked Execution](/en/broker/postgres-execution) - Long-running execution, state machine, leases
- [Migrate to onestep-sql](/en/guide/migrate-to-onestep-sql) - Migration guide from `onestep-mysql` / `onestep-postgres`
- [YAML Task Definition](/en/yaml-task-definition) - Plugin resource registration and strict validation
- [Core Reliability](/en/core-reliability) - At-least-once and duplicate output semantics
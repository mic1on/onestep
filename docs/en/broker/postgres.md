---
title: PostgreSQL | Broker
outline: deep
---

# PostgreSQL

`onestep-postgres` provides PostgreSQL table queue, incremental polling, table sink, and SQLAlchemy-backed state/cursor storage. The first release does not include logical replication or CDC.

For the full business integration flow for long-running tasks, see [PostgreSQL Tracked Execution](/broker/postgres-execution).

## Installation

```bash
pip install onestep-postgres
```

## Basic Usage

```python
from onestep import OneStepApp
from onestep_postgres import PostgresConnector

app = OneStepApp("pg-sync")
pg = PostgresConnector("postgresql+psycopg://user:pass@localhost/app")

cursor = pg.cursor_store(table="onestep_cursor")
source = pg.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    state=cursor,
)
sink = pg.table_sink(
    table="processed_users",
    mode="upsert",
    keys=("id",),
)


@app.task(source=source, emit=sink, concurrency=4)
async def sync_user(ctx, row):
    return {
        "id": row["id"],
        "name": row["name"],
        "updated_at": row["updated_at"],
    }
```

## Table Queue

Table queue uses PostgreSQL row locks to claim tasks, suitable for using a database table as a durable queue.

```python
source = pg.table_queue(
    table="jobs",
    key="id",
    where="status = 'pending'",
    claim={"status": "processing"},
    ack={"status": "done"},
    nack={"status": "pending"},
    batch_size=100,
)
```

## YAML Configuration

After installing the plugin, YAML can use `postgres*` resource types:

```yaml
resources:
  pg:
    type: postgres
    dsn: "${POSTGRES_DSN}"

  cursor:
    type: postgres_cursor_store
    connector: pg

  users:
    type: postgres_incremental
    connector: pg
    table: users
    key: id
    cursor: [updated_at, id]
    state: cursor

  processed:
    type: postgres_table_sink
    connector: pg
    table: processed_users
    mode: upsert
    keys: [id]

tasks:
  - name: sync_users
    source: users
    emit: processed
    handler:
      ref: worker.tasks:sync_user
```

## Notes

- Incremental polling persists the cursor after delivery `ack()`.
- `table_sink(mode="upsert")` requires configuring `keys`.
- For CDC, continue using MySQL binlog or design a separate logical replication flow for PostgreSQL.

## Tracked Long-Running Execution

`onestep-postgres` can also use PostgreSQL as the single source of truth for task state, results, and leases. FastAPI uses the core `ExecutionClient`, and workers use `PostgresExecutionSource` directly:

```python
from onestep import ExecutionClient
from onestep_postgres import PostgresExecutionBackend, PostgresExecutionSource

backend = PostgresExecutionBackend(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=True,
    reclaim_batch_size=100,
)
step = ExecutionClient(backend, namespace="agent-api")

async with step:
    execution = await step.submit(
        "run_agent",
        payload,
        idempotency_key=request_id,
    )
```

```python
source = PostgresExecutionSource(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=False,
    namespace="agent-api",
    task_names=("run_agent",),
    worker_id="agent-worker-1",
)
```

Each execution source can only bind to one task name and must match the app task name bound to that source. To handle multiple tasks, create a separate source for each task.

States include `queued`, `running`, `retrying`, `succeeded`, `failed`,
`cancel_requested`, `cancelled`, and `expired`. `Execution` is an immutable snapshot
and does not auto-refresh on property access; call `get()` or `list()` again for the latest
state. `result()` queries once and raises `ExecutionNotReady` if incomplete, or the
corresponding exception type for failure, cancellation, or expiry; succeeded results
may be `None`.

Default payload/result inline limits are 1 MiB each, metadata limit is 64 KiB.
Task submissions are deduplicated by namespace, task name, and idempotency key;
identical content returns the original record, different content raises a conflict.
Lease requires `0 < heartbeat_interval_s <= lease_duration_s / 3`. The system is
at-least-once; cancellation is cooperative, and handler external side effects still
require business idempotency. Production deployment should create tables first and
use `auto_create=False` to avoid requiring DDL permissions at runtime.

The managed completion path writes the handler return value to the success record.
Calling the traditional `ack()` on an execution delivery can only record
`succeeded` with `result=None`, since the public `Delivery.ack()` API has no
result parameter.
If the worker submits `succeeded` when the execution is already `cancel_requested`,
cancel-wins: the execution ends as `cancelled`, the worker's result/error is not
written; the corresponding attempt is recorded as `cancelled`, `error=NULL`, and the
attempt table does not save the result. This is an intentional historical semantic and
does not mean the handler had no return value.

The worker performs limited exponential backoff on retryable PostgreSQL heartbeat
errors while the lease is still valid; non-retryable errors, expired leases, or
exhausted retries cancel the current processing task. Expired executions and stuck
leases are recovered by the source's `claim()` — there is no independent reaper.
Each `claim()` processes up to `reclaim_batch_size` records per stuck state category;
active polling drains backlogs batch by batch. Without worker polling, states remain
until the next claim. `PostgresConnector.secret_tokens()` returns independent copies
for error redaction; callers should not log their contents.

## Next Steps

- [YAML Task Definition](/yaml-task-definition) - View plugin resource registration and strict validation
- [Core Reliability](/core-reliability) - Understand at-least-once and duplicate output semantics

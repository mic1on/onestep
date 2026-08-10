# onestep-postgres

PostgreSQL connector plugin for onestep.

Install it with:

```bash
pip install onestep-postgres
```

YAML resources are available after the plugin is installed:

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
```

The plugin supports table queues, incremental polling, table sinks,
SQLAlchemy-backed state/cursor stores, and tracked PostgreSQL executions.

```python
from onestep import ExecutionClient
from onestep_postgres import PostgresExecutionBackend

backend = PostgresExecutionBackend(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=True,
    reclaim_batch_size=100,
)
step = ExecutionClient(backend, namespace="agent-api")

async with step:
    execution = await step.submit("run_agent", payload, idempotency_key=request_id)

source = backend.source(
    namespace="agent-api",
    task_names=("run_agent",),
    worker_id="agent-worker-1",
)
```

`PostgresConnector` remains available when an application also needs table queues,
sinks, state stores, or one shared pool. In that advanced path use
`PostgresExecutionBackend.from_connector(pg, ...)`; the connector remains owned and
closed by the caller. For the direct DSN path, `ExecutionClient` and
`PostgresExecutionSource` manage the backend lifecycle for their process.

Each execution source accepts exactly one task name, which must match the app
task bound to that source. Create a separate source for each additional task.

Execution statuses are `queued`, `running`, `retrying`, `succeeded`, `failed`,
`cancel_requested`, `cancelled`, and `expired`. Inline payload and result values
are limited to 1 MiB each; metadata is limited to 64 KiB. Use
`auto_create=False` after deployment migrations. Execution is at-least-once
and cancellation is cooperative; make handler side effects idempotent.

Managed runtime completion persists the handler result. Calling the execution
delivery's legacy `ack()` directly records `succeeded` with `result=None`
because the public `Delivery.ack()` API has no result argument. Retryable
heartbeat failures use bounded exponential backoff while the lease remains
valid. Stale recovery is claim-driven rather than handled by an independent
reaper: each claim processes at most `reclaim_batch_size` records per stale
state category, so active workers drain a backlog incrementally. Connector
errors use an independent copy returned by `PostgresConnector.secret_tokens()`
for redaction.

The plugin does not support PostgreSQL logical replication or CDC.

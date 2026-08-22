---
title: 'Case: FastAPI Submits Long Tasks and Schedules Workers | Guide'
outline: deep
---

# Case: FastAPI Submits Long Tasks and Schedules Workers

This case shows how a business API (FastAPI) submits long-running tasks to OneStep,
executed asynchronously by a separate worker process, while the API only returns a
task ID, queries status, and cancels. It suits "request-triggered, background
long-running" scenarios such as agent calls, report generation, file processing, and
bulk import.

Scheduling storage uses PostgreSQL: the API process submits, queries, and cancels via
`ExecutionClient`, and the worker process claims, executes, and heartbeats via
`PostgresExecutionSource`. The two processes are decoupled only through the same
PostgreSQL database.

```text
FastAPI (ExecutionClient)          OneStep worker (PostgresExecutionSource)
POST /executions        ──┐        claim run_agent task
GET  /executions/{id}     ├─ PostgreSQL ─┤ OneStepApp + handler
POST /.../cancel        ──┘        heartbeat + managed completion
```

::: warning Do not run the worker inside the API process
Do not `await` task completion within the FastAPI (or Django) request lifecycle, and
do not start a OneStep worker inside the API process. The API and worker must be two
separate processes.
:::

## Goals & Boundaries

- On receiving a request, the API returns an execution ID immediately; the business
  side then polls status or reads the result, rather than waiting inside a long-lived
  HTTP connection.
- Submission carries an `Idempotency-Key`: submitting the same key repeatedly returns
  the same execution without re-enqueuing.
- Semantics are at-least-once: after a worker crash the lease expires and the task is
  re-claimed for execution. Downstream writes inside the handler must dedup by
  `execution_id` or a business idempotency key.
- One `PostgresExecutionSource` binds exactly one task name; create a separate source
  per task when executing multiple tasks.

## Prerequisites

The release order requires core before the plugin:

```bash
pip install "onestep>=1.9.0" "onestep-sql[postgres]>=0.1.0" fastapi uvicorn
```

`onestep-sql[postgres]` depends on `onestep>=1.9.0`. Installing core alone does not
enable tracked execution; only a process with the PostgreSQL plugin installed can
submit/claim.

## 1. One-time Database Initialization

The execution backend uses two tables: `onestep_executions` (task main record) and
`onestep_execution_attempts` (one attempt per claim). Run once at deploy time with a
connection that has DDL permission, then use `auto_create=False` at runtime.

```python
# deploy/create_execution_tables.py
import asyncio
import os

from onestep_sql.postgres import PostgresExecutionBackend


async def main() -> None:
    backend = PostgresExecutionBackend(
        dsn=os.environ["POSTGRES_EXECUTION_MIGRATION_DSN"],
        auto_create=True,
    )
    await backend.open()
    await backend.close()


asyncio.run(main())
```

The runtime identity should not have DDL permission; it needs at least
`SELECT, INSERT, UPDATE` on both tables and `USAGE` on the target schema.

## 2. Shared Configuration

The API and worker share at least the following configuration, and `namespace` must
match:

```bash
POSTGRES_EXECUTION_DSN=postgresql+psycopg://app_runtime:***@db.example.com/app
POSTGRES_EXECUTION_NAMESPACE=agent-api
```

`namespace` is a business routing and query boundary (not a database permission
boundary); task name is the routing key, and the task name at submit must exactly
match the worker source's task name.

## 3. API Process (FastAPI)

The API submits, queries, and cancels via `ExecutionClient`. The example only shows
the onestep boundaries; production projects should use their own request models and
authorization.

```python
# app/api.py
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from onestep import (
    Execution,
    ExecutionClient,
    ExecutionConflict,
    ExecutionEncodingError,
    ExecutionNotFound,
    ExecutionNotReady,
    ExecutionStatus,
)
from onestep_sql.postgres import PostgresExecutionBackend

backend = PostgresExecutionBackend(
    dsn=os.environ["POSTGRES_EXECUTION_DSN"],
    auto_create=False,
)
executions = ExecutionClient(
    backend,
    namespace=os.getenv("POSTGRES_EXECUTION_NAMESPACE", "agent-api"),
)
ALLOWED_TASKS = {"run_agent"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with executions:
        yield


api = FastAPI(lifespan=lifespan)


class SubmitBody(BaseModel):
    task_name: str
    payload: Any
    metadata: dict[str, Any] = Field(default_factory=dict)


def view(e: Execution) -> dict[str, Any]:
    return {
        "id": str(e.id),
        "task_name": e.task_name,
        "status": e.status.value,
        "attempts": e.attempts,
    }


@api.post("/v1/executions", status_code=202)
async def submit(
    body: SubmitBody,
    idempotency_key: str = Header(..., alias="Idempotency-Key", min_length=1),
) -> dict[str, Any]:
    if body.task_name not in ALLOWED_TASKS:
        raise HTTPException(status_code=422, detail="unsupported task_name")
    try:
        execution = await executions.submit(
            body.task_name,
            body.payload,
            idempotency_key=idempotency_key,
            metadata=body.metadata,
        )
    except ExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ExecutionEncodingError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return view(execution)


@api.get("/v1/executions/{execution_id}")
async def get(execution_id: UUID) -> dict[str, Any]:
    execution = await executions.get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return view(execution)


@api.get("/v1/executions/{execution_id}/result")
async def result(execution_id: UUID) -> dict[str, Any]:
    try:
        return {"result": await executions.result(execution_id)}
    except ExecutionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExecutionNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@api.post("/v1/executions/{execution_id}/cancel")
async def cancel(execution_id: UUID) -> dict[str, Any]:
    execution = await executions.cancel(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return view(execution)
```

State machine: `queued`, `running`, `retrying`, `cancel_requested` are non-terminal
states you may keep waiting on; `succeeded`, `failed`, `cancelled`, `expired` are
terminal. Poll with backoff (e.g. 1, 2, 4, 8 seconds then settle at 10–30 seconds)
and set a total wait cap.

## 4. Worker Process

The worker claims tasks via `PostgresExecutionSource`, and `OneStepApp` handles
scheduling, retries, heartbeat, and shutdown. The handler return value is written by
the managed runtime into the execution's `result`; **do not** call
`ack()` / `retry()` / `fail()` manually.

```python
# app/worker.py
import os
from typing import Any

from onestep import ExponentialBackoff, OneStepApp
from onestep_sql.postgres import PostgresExecutionSource

app = OneStepApp("agent-worker", shutdown_timeout_s=30.0)

jobs = PostgresExecutionSource(
    dsn=os.environ["POSTGRES_EXECUTION_DSN"],
    auto_create=False,
    namespace=os.getenv("POSTGRES_EXECUTION_NAMESPACE", "agent-api"),
    task_names=("run_agent",),
    batch_size=4,
    poll_interval_s=1.0,
    lease_duration_s=90.0,
    heartbeat_interval_s=30.0,
    worker_id=os.getenv("HOSTNAME", "agent-worker-local"),
)


@app.task(
    name="run_agent",
    source=jobs,
    concurrency=4,
    retry=ExponentialBackoff(
        max_attempts=3,
        min_delay_s=2.0,
        max_delay_s=30.0,
        jitter="full",
    ),
    timeout_s=1800.0,
)
async def run_agent(ctx, payload: dict[str, Any]) -> dict[str, Any]:
    execution_meta = ctx.current.meta.get("onestep.execution", {})
    execution_id = execution_meta.get("id")
    # Downstream writes must dedup by execution_id or a business key (at-least-once).
    result = await run_agent_model(payload, execution_id=execution_id)
    return {"execution_id": execution_id, "output": result}


async def run_agent_model(payload, *, execution_id):
    # Replace with business logic; do not call delivery.ack() manually.
    return {"document_id": payload["document_id"], "summary": "..."}
```

Start and check:

```bash
onestep check app.worker:app
onestep run app.worker:app
```

`heartbeat_interval_s` must satisfy `0 < heartbeat_interval_s <= lease_duration_s / 3`.
If the handler is a synchronous blocking function, isolate it with
`asyncio.to_thread()` to avoid blocking the heartbeat task.

## Operation & Recovery

### Idempotency and at-least-once

- **Submission idempotency**: the `Idempotency-Key` makes repeated submissions return
  the same execution without re-enqueuing.
- **Execution at-least-once**: after a worker crash the lease expires and the task is
  re-claimed. The handler's downstream side effects must dedup by `execution_id`,
  otherwise they duplicate.

### Lease, heartbeat, and fencing

After claiming a task the worker holds a lease and renews it via heartbeat. When the
heartbeat stops (process hangs/crashes) the lease expires and the task is re-claimed
by another worker; late writes from the old worker are rejected by fencing. The
PostgreSQL database time is used as the authoritative lease clock.

### Cancellation

`POST /.../cancel` sets the execution to `cancel_requested`. A running handler
observes the cancel request via `ctx` and exits gracefully; already-terminal
executions are unaffected.

### Deployment order

1. Initialize the database tables first.
2. Deploy the worker first and confirm it can connect to the database.
3. Then open the API submission entry point.
4. Avoid running mixed versions on the same execution chain for long.

## Trade-offs

| Parameter | Value here | Trade-off |
|---|---:|---|
| `batch_size` (source) | 4 | Tasks claimed per fetch; tune to worker concurrency. |
| `concurrency` | 4 | Concurrent handler executions. |
| `lease_duration_s` | 90 | Lease length, must exceed one execution's heartbeat window. |
| `heartbeat_interval_s` | 30 | Must be ≤ lease/3 to renew in time. |
| `timeout_s` | 1800 | Single-execution cap; timeouts follow the retry policy. |
| `max_attempts` | 3 | Transient failures retry; exhaustion sets execution to `failed`. |

## Related

- [PostgreSQL Tracked Execution](/en/broker/postgres-execution) (full API, state machine, schema)
- [PostgreSQL Connector](/en/broker/postgres)
- [Retry & Dead Letter](/en/core/retry)
- [Core Reliability](/en/core-reliability)

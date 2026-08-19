---
title: PostgreSQL Tracked Execution
outline: deep
---

# PostgreSQL Tracked Execution

This document explains how business systems can use PostgreSQL as a submission, state, result, cancellation, and lease store for long-running tasks after the release of `onestep==1.9.0` and `onestep-postgres==0.2.0`.

Use cases: An HTTP request submits a task that may run for seconds, minutes, or longer. The API returns a task ID, and the business side polls for status or results. Typical examples include Agents, report generation, file processing, async imports, and batch syncs.

This feature is optional. Existing `MemoryQueue`, RabbitMQ, Redis, SQS, scheduled tasks, and PostgreSQL table queue integrations require no changes.

## 0. Confirm Whether Both Packages Are Needed

| Business Scenario | Required Versions | Business Code Changes Needed |
| --- | --- | --- |
| Continue using regular queue, schedule, webhook | `onestep==1.9.0` | No |
| Continue using existing PostgreSQL table queue, incremental, state or sink | `onestep==1.9.0` + compatible PostgreSQL plugin | Usually no |
| Use submission, query, result, and cancellation from this page | `onestep==1.9.0` + `onestep-postgres==0.2.0` | Deploy API and worker per this page |

`onestep-postgres==0.2.0` depends on `onestep>=1.9.0` and cannot be combined with `onestep==1.8.1`. Conversely, installing `onestep==1.9.0` alone does not automatically enable tracked execution; workers without the PostgreSQL plugin can run normally.

## 1. Runtime Architecture

The API process handles submission, queries, and cancellation. The OneStep worker process claims and executes. Both processes collaborate through the same PostgreSQL database. Do not start a OneStep worker inside a FastAPI or Django process.

```text
Business API                         OneStep worker
POST /executions                     PostgresExecutionSource
GET  /executions/{id}                OneStepApp + handler
POST /executions/{id}/cancel         heartbeat + lease completion
        |                                      |
        +--------- PostgreSQL -----------------+
                  executions + attempts
```

Core object responsibilities:

| Object | Process | Purpose |
| --- | --- | --- |
| `ExecutionClient` | API | Submit, query, paginate, cancel, read results |
| `PostgresExecutionBackend` | API, advanced shared pool scenarios | Connect to the same execution tables |
| `PostgresExecutionSource` | worker | Claim tasks by namespace and task name |
| `OneStepApp` | worker | Schedule handler, retry, cancellation, and shutdown |

Each `PostgresExecutionSource` can only bind to one task name. To execute multiple tasks, create a separate source for each task.

## 2. Release and Installation

After both packages are released, API and worker processes participating in the same execution chain use the same locked versions:

```bash
pip install "onestep>=1.9.0" "onestep-postgres>=0.2.0"
```

Or use the core extra. Note that the extra declares `onestep-postgres>=0.2.0`; production environments should still pin the final resolved version via lockfile:

```bash
pip install "onestep[postgres]>=1.9.0"
```

When using uv:

```bash
uv add "onestep>=1.9.0" "onestep-postgres>=0.2.0"
uv run python -c "import onestep, onestep_postgres; print(onestep.__version__, onestep_postgres.__version__)"
uv run pip check
```

Release order:

1. Release `onestep==1.9.0`.
2. Confirm `onestep==1.9.0` is installable from PyPI.
3. Release `onestep-postgres==0.2.0`.
4. Lock dependencies, complete database initialization.
5. Deploy the worker first and confirm it can connect to the database before opening the API submission endpoint.
6. Avoid running mixed versions in the same business chain for extended periods.

If the plugin is not yet released, `onestep[postgres]>=1.9.0` and `onestep[all]>=1.9.0` may not resolve dependencies fully. Plain `onestep>=1.9.0` does not depend on the PostgreSQL plugin and can be installed independently.

## 3. Database Initialization

The execution backend uses two tables:

- `onestep_executions`: Task main record, state, payload, result, error, and current lease.
- `onestep_execution_attempts`: One attempt per claim, recording worker, heartbeats, and terminal state.

Production should create tables via a migration role and use `auto_create=False` at runtime. The PR provides SQLAlchemy create-only initialization; it does not perform safe column changes or version migrations on existing tables with the same name.

### 3.1 One-Time Initialization Script

Execute once during deployment using a separate connection with DDL privileges:

```python
# deploy/create_execution_tables.py
import asyncio
import os

from onestep_postgres import PostgresExecutionBackend


async def main() -> None:
    backend = PostgresExecutionBackend(
        dsn=os.environ["POSTGRES_EXECUTION_MIGRATION_DSN"],
        table=os.getenv("POSTGRES_EXECUTIONS_TABLE", "onestep_executions"),
        attempts_table=os.getenv(
            "POSTGRES_EXECUTION_ATTEMPTS_TABLE",
            "onestep_execution_attempts",
        ),
        auto_create=True,
    )
    await backend.open()
    await backend.close()


asyncio.run(main())
```

After successful execution, both API and worker use `auto_create=False`. If custom table names are used, the initialization script, API, and worker must be fully consistent.

`table` and `attempts_table` only accept SQL identifiers without schema prefixes, e.g., `onestep_executions`, not `app.onestep_executions`. If using a non-`public` schema, configure a consistent PostgreSQL `search_path` for both migration and runtime connections, then continue using schema-free table names.

### 3.2 Runtime Database Permissions

The runtime identity should not have DDL permissions. An execution-only scenario needs at least SELECT, INSERT, and UPDATE permissions on both tables, plus `USAGE` on the target schema. If the project also uses PostgreSQL table queue, state store, or sink, grant corresponding permissions for those resources as well.

```sql
GRANT USAGE ON SCHEMA public TO onestep_runtime;
GRANT SELECT, INSERT, UPDATE
ON TABLE public.onestep_executions, public.onestep_execution_attempts
TO onestep_runtime;
```

Pre-production check:

```sql
SELECT to_regclass('public.onestep_executions');
SELECT to_regclass('public.onestep_execution_attempts');
```

If the tables already exist but from another version, do not simply set `auto_create=False` and continue. Use a separate migration role to verify columns, constraints, and indexes.

## 4. Shared Configuration

API and worker share at least the following configuration:

```bash
POSTGRES_EXECUTION_DSN=postgresql+psycopg://app_runtime:***@db.example.com/app
POSTGRES_EXECUTION_NAMESPACE=agent-api
POSTGRES_EXECUTIONS_TABLE=onestep_executions
POSTGRES_EXECUTION_ATTEMPTS_TABLE=onestep_execution_attempts
```

Do not write DSN, passwords, or tokens into code, YAML plaintext, or logs. `PostgresConnector` provides redacted tokens to connector errors, but business logs should still avoid printing DSNs.

The namespace is a business isolation boundary. API and worker must use the same namespace; other businesses can share the same database using different namespaces. The task name is a routing key; the task name at submission must exactly match the worker source's task name.

The namespace is a logical routing and query boundary, not a database permission boundary. Tenants requiring strong isolation should use separate databases, schemas/roles, or implement authorization at the business API layer, not rely solely on namespace strings.

## 5. API Process

Below is a FastAPI example. Production projects should use their own request models and authorization logic; the example only shows the onestep boundaries.

```python
# app/api.py
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from onestep import (
    Execution,
    ExecutionCancelled,
    ExecutionClient,
    ExecutionConflict,
    ExecutionEncodingError,
    ExecutionFailed,
    ExecutionExpired,
    ExecutionNotFound,
    ExecutionNotReady,
    ExecutionStatus,
)
from onestep_postgres import PostgresExecutionBackend
from pydantic import BaseModel, Field


backend = PostgresExecutionBackend(
    dsn=os.environ["POSTGRES_EXECUTION_DSN"],
    table=os.getenv("POSTGRES_EXECUTIONS_TABLE", "onestep_executions"),
    attempts_table=os.getenv(
        "POSTGRES_EXECUTION_ATTEMPTS_TABLE",
        "onestep_execution_attempts",
    ),
    auto_create=False,
)
executions = ExecutionClient(
    backend,
    namespace=os.getenv("POSTGRES_EXECUTION_NAMESPACE", "agent-api"),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with executions:
        yield


api = FastAPI(lifespan=lifespan)
ALLOWED_TASKS = {"run_agent"}


class SubmitExecutionBody(BaseModel):
    task_name: str
    payload: Any
    metadata: dict[str, Any] = Field(default_factory=dict)
    delay_s: float | None = None
    expires_at: datetime | None = None


class CancelExecutionBody(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


def execution_view(execution: Execution) -> dict[str, Any]:
    return {
        "id": str(execution.id),
        "namespace": execution.namespace,
        "task_name": execution.task_name,
        "status": execution.status.value,
        "attempts": execution.attempts,
        "metadata": dict(execution.metadata),
        "version": execution.version,
        "created_at": execution.created_at.isoformat(),
        "available_at": execution.available_at.isoformat(),
        "started_at": (
            None if execution.started_at is None else execution.started_at.isoformat()
        ),
        "finished_at": (
            None if execution.finished_at is None else execution.finished_at.isoformat()
        ),
        "cancel_requested_at": (
            None
            if execution.cancel_requested_at is None
            else execution.cancel_requested_at.isoformat()
        ),
        "expires_at": (
            None if execution.expires_at is None else execution.expires_at.isoformat()
        ),
        "error": (
            None
            if execution.error is None
            else {
                "kind": execution.error.kind,
                "exception_type": execution.error.exception_type,
                "stage": execution.error.stage,
                "backend": execution.error.backend,
                "operation": execution.error.operation,
                "connector_kind": execution.error.connector_kind,
            }
        ),
    }


@api.post("/v1/executions", status_code=202)
async def submit_execution(
    body: SubmitExecutionBody,
    idempotency_key: str = Header(
        ...,
        alias="Idempotency-Key",
        min_length=1,
        max_length=255,
    ),
) -> dict[str, Any]:
    if body.task_name not in ALLOWED_TASKS:
        raise HTTPException(status_code=422, detail="unsupported task_name")
    if body.expires_at is not None and body.expires_at.tzinfo is None:
        raise HTTPException(status_code=422, detail="expires_at must include a timezone")
    try:
        execution = await executions.submit(
            body.task_name,
            body.payload,
            idempotency_key=idempotency_key,
            metadata=body.metadata,
            delay_s=body.delay_s,
            expires_at=body.expires_at,
        )
    except ExecutionConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (ExecutionEncodingError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return execution_view(execution)


@api.get("/v1/executions")
async def list_executions(
    task_name: str | None = None,
    status: ExecutionStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> dict[str, Any]:
    try:
        page = await executions.list(
            task_name=task_name,
            status=status,
            limit=limit,
            cursor=cursor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "items": [execution_view(item) for item in page.items],
        "next_cursor": page.next_cursor,
    }


@api.get("/v1/executions/{execution_id}")
async def get_execution(execution_id: UUID) -> dict[str, Any]:
    execution = await executions.get(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return execution_view(execution)


@api.post("/v1/executions/{execution_id}/cancel")
async def cancel_execution(
    execution_id: UUID,
    body: CancelExecutionBody,
) -> dict[str, Any]:
    execution = await executions.cancel(
        execution_id,
        reason=body.reason,
    )
    if execution is None:
        raise HTTPException(status_code=404, detail="execution not found")
    return execution_view(execution)


@api.get("/v1/executions/{execution_id}/result")
async def get_execution_result(execution_id: UUID) -> dict[str, Any]:
    try:
        return {"result": await executions.result(execution_id)}
    except ExecutionNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ExecutionNotReady as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExecutionCancelled as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ExecutionExpired as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except ExecutionFailed as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
```

### 5.1 Submit Request

```http
POST /v1/executions
Content-Type: application/json
Idempotency-Key: req-20260810-0001

{
  "task_name": "run_agent",
  "payload": {
    "prompt": "summarize this document",
    "document_id": "doc-123"
  },
  "metadata": {
    "tenant_id": "tenant-a",
    "requested_by": "user-42"
  }
}
```

The business API should use a stable business request ID as `idempotency_key`. Resubmitting the same namespace, task name, and idempotency key with identical content returns the original execution; if payload, metadata, or other submission parameters differ, `ExecutionConflict` is raised, typically mapped to HTTP 409.

The API above requires the `Idempotency-Key` header; the underlying `ExecutionClient` allows omitting it, but it is not recommended for network-facing requests. Do not expose arbitrary task names to external callers; use an allowlist as in the example, or fix the task name per endpoint. Authentication info like `tenant_id`, `requested_by` should be injected server-side, not trusted from the request body.

`Execution` is an immutable snapshot. `submit()` returns the submission-time snapshot; `get()` returns the query-time snapshot; neither auto-refreshes on property access.

Pagination uses keyset cursors: pass the `next_cursor` from the response as-is to the next `GET /v1/executions?cursor=...`. The cursor is opaque; business code should not parse or modify it.

### 5.2 Data Types and Size Limits

Default limits are calculated on encoded JSON size: payload 1 MiB, metadata 64 KiB, result 1 MiB. Large files, model contexts, and binary artifacts should be stored in object storage first, submitting only URIs, checksums, and necessary metadata to execution.

HTTP business typically submits standard JSON. Python clients also support limited tagged types, e.g., timezone-aware `datetime`, `UUID`, `Decimal`, `bytes`, tuple, set, and replayable Enum; arbitrary Python objects are not supported, dict keys must be strings, and floats cannot be NaN or Infinity. Encoding failures or exceeding limits raises `ExecutionEncodingError`.

The metadata key `onestep.execution` is reserved by the runtime and cannot be used by business submissions. Handler return values must also satisfy the same encoding constraints; test with the largest result sample before going live.

### 5.3 Business Call Flow

After a successful submission, save the execution ID from the response. The business side should not hold database transactions or HTTP long connections waiting for the handler; instead, poll for status, receive custom notifications, or read results later.

```bash
# 1. Submit; if network times out, retry with the same Idempotency-Key and identical body
curl -X POST https://api.example.com/v1/executions \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: req-20260810-0001' \
  -d '{"task_name":"run_agent","payload":{"document_id":"doc-123"}}'

# 2. Query status
curl https://api.example.com/v1/executions/2a31b3a6-72c9-4ae3-8e8e-4d6f78c00f3a

# 3. Read result after succeeded
curl https://api.example.com/v1/executions/2a31b3a6-72c9-4ae3-8e8e-4d6f78c00f3a/result

# 4. Cancel when no longer needed; running state won't stop immediately
curl -X POST \
  https://api.example.com/v1/executions/2a31b3a6-72c9-4ae3-8e8e-4d6f78c00f3a/cancel \
  -H 'Content-Type: application/json' \
  -d '{"reason":"user left the page"}'
```

Recommended polling backoff: 1, 2, 4, 8 seconds, then fixed at 10 to 30 seconds, with a total wait ceiling for the client. `queued`, `running`, `retrying`, and `cancel_requested` are non-terminal states that can continue waiting; only `succeeded`, `failed`, `cancelled`, and `expired` are terminal.

## 6. Worker Process

```python
# app/worker.py
import os
from typing import Any

from onestep import ExponentialBackoff, OneStepApp
from onestep_postgres import PostgresExecutionSource


app = OneStepApp("agent-worker", shutdown_timeout_s=30.0)
jobs = PostgresExecutionSource(
    dsn=os.environ["POSTGRES_EXECUTION_DSN"],
    table=os.getenv("POSTGRES_EXECUTIONS_TABLE", "onestep_executions"),
    attempts_table=os.getenv(
        "POSTGRES_EXECUTION_ATTEMPTS_TABLE",
        "onestep_execution_attempts",
    ),
    auto_create=False,
    reclaim_batch_size=100,
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

    # Downstream writes must use execution_id or business idempotency key for dedup.
    result = await run_agent_model(
        payload,
        execution_id=execution_id,
    )
    return {"execution_id": execution_id, "output": result}


async def run_agent_model(
    payload: dict[str, Any],
    *,
    execution_id: str | None,
) -> Any:
    # Replace this with business logic. Do not call delivery.ack() manually.
    return {"document_id": payload["document_id"], "summary": "..."}


```

Startup and check:

```bash
onestep check app.worker:app
onestep run app.worker:app
```

The handler return value is written to the execution's `result` by the managed runtime. Business handlers should not manually call `ack()`, `retry()`, or `fail()`.

Python worker's `OneStepApp` opens and closes the source.
`PostgresExecutionSource` with a direct DSN creates and closes its own connection pool lazily
in the current worker process. To share a `PostgresConnector` with table queue, sink, or
state store, use `PostgresExecutionSource.from_connector(pg, ...)` or create
`PostgresExecutionBackend.from_connector(pg, ...)` first; the shared connector is still
closed by the caller, and YAML resource lifecycle is handled by the app resource manager.

If the handler is a synchronous blocking function, use `asyncio.to_thread()` or other
thread pool isolation to ensure the heartbeat task can keep running. `heartbeat_interval_s`
must satisfy:

```text
0 < heartbeat_interval_s <= lease_duration_s / 3
```

Multiple worker replicas can use the same source configuration, but `worker_id` should use
pod name, hostname, or another instance-unique identifier for lease and attempt diagnosis.

For multi-process or pre-fork deployment, each process should use
`PostgresExecutionSource(dsn=...)` or `PostgresExecutionBackend(dsn=...)`.
The DSN approach is lazy; even if the object is created before fork, connection pools are
independently created in child processes. Do not share external connection pools from
`PostgresConnector` or `from_connector()` across processes; create a connector inside each
child process. Database max connections must be calculated across all API/worker processes
and each process's pool configuration.

## 7. YAML Worker Configuration

If the worker uses YAML, the API still uses Python `ExecutionClient`. YAML only handles worker wiring, not HTTP API.

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: agent-worker

resources:
  pg:
    type: postgres
    dsn: "${POSTGRES_EXECUTION_DSN}"

  agent_jobs:
    type: postgres_execution_source
    connector: pg
    namespace: agent-api
    task_names: [run_agent]
    table: onestep_executions
    attempts_table: onestep_execution_attempts
    batch_size: 4
    poll_interval_s: 1.0
    lease_duration_s: 90.0
    heartbeat_interval_s: 30.0
    worker_id: "${HOSTNAME:-agent-worker}"
    auto_create: false
    reclaim_batch_size: 100

tasks:
  - name: run_agent
    source: agent_jobs
    handler:
      ref: app.handlers:run_agent
    concurrency: 4
    retry:
      type: exponential_backoff
      max_attempts: 3
      min_delay_s: 2.0
      max_delay_s: 30.0
      jitter: full
    timeout_s: 1800.0
```

Validation:

```bash
onestep check --strict worker.yaml
onestep run worker.yaml
```

## 8. State and Business Semantics

| State | Meaning | Business Handling |
| --- | --- | --- |
| `queued` | Submitted, waiting for worker | Query or continue waiting |
| `running` | Claimed by a worker | Query progress, cancel if needed |
| `retrying` | Handler failed, waiting for next attempt | Continue waiting |
| `succeeded` | Handler succeeded, result persisted | Call result endpoint |
| `failed` | Max attempts exceeded or explicit failure | Show failure and handle manually |
| `cancel_requested` | Running task received cancellation request | Wait for worker to converge |
| `cancelled` | Task cancelled | Do not read result |
| `expired` | Exceeded business expires_at before claiming | Resubmit or handle manually |

`expires_at` is the "latest start processing time", not a runtime deadline: tasks already claimed by a healthy worker can continue past this time. To limit single handler runtime, use the task's `timeout_s`.

`result()` exception mapping recommendations:

| Exception | Meaning | Example HTTP Status |
| --- | --- | --- |
| `ExecutionNotFound` | Execution does not exist | 404 |
| `ExecutionNotReady` | No terminal state yet | 409 or 202 |
| `ExecutionFailed` | Terminal state is failed | 422 or business-defined failure status |
| `ExecutionCancelled` | Terminal state is cancelled | 409 |
| `ExecutionExpired` | Terminal state is expired | 410 |

Cancellation is cooperative:

1. queued/retrying states directly become `cancelled`.
2. running state first becomes `cancel_requested`.
3. The worker's heartbeat observes the cancellation and cancels the handler task.
4. After the worker completes cancellation convergence, it becomes `cancelled`.

If cancellation and handler success happen at the same time, cancel-wins. The execution does not save the handler's result/error; the corresponding attempt is `cancelled`, `error` is NULL, and no result is saved. This is intentional and does not mean the handler had no return value.

## 9. Retry, Lease, and Duplicate Execution

The system is at-least-once, not exactly-once. The following scenarios may cause the handler or external side effects to execute again:

- Worker crashes after external writes but before completing the execution;
- Lease expires and another worker takes over;
- Database connection drops during result commit, and the business side cannot determine if the commit succeeded;
- Handler enters the next attempt per retry policy.

Downstream writes must use execution ID or business idempotency keys for dedup. For example:

```sql
CREATE UNIQUE INDEX uq_business_result_request
ON business_results (request_id);
```

Do not treat "result not found" as "task definitely did not execute". If the API connection drops after submission, retry with the same `idempotency_key`, not a new request ID.

Recommended lease parameters:

| Parameter | Default | Recommendation |
| --- | --- | --- |
| `lease_duration_s` | 90 | Adjust based on normal database jitter and heartbeat latency |
| `heartbeat_interval_s` | 30 | No more than one third of lease duration |
| `reclaim_batch_size` | 100 | Adjust based on database load and recovery speed |
| `batch_size` | 100 | Should generally not be many times larger than worker concurrency |
| `poll_interval_s` | 1 | Affects claim latency when idle |

Expired executions and stuck leases are recovered by the next `claim()` — there is no independent reaper. When all workers are stopped, no reclaim happens; after workers resume, backlogs are processed in batches of `reclaim_batch_size`.

Lease deadlines and expiry checks use PostgreSQL current transaction time, independent of worker process clocks; the source's heartbeat retry also calculates remaining lease via database time. SQLite tests and compatibility paths use an injected `clock`.

## 10. Observability and Troubleshooting

Execution source places correlation info in envelope metadata; handlers can read:

```python
execution_meta = ctx.current.meta["onestep.execution"]
execution_id = execution_meta["id"]
attempt_id = execution_meta["attempt_id"]
```

TaskEvent also carries the same correlation metadata. Log at minimum:

- `execution_id`
- `attempt_id`
- `task_name`
- `worker_id`
- Current execution status
- Business idempotency key or request ID

Execution's structured error only contains kind, exception type, failure stage, and connector classification, not the original exception message or traceback. If the business needs searchable detailed diagnostics, log them in the handler and correlate with `execution_id`, `attempt_id`, while following sensitive information redaction rules.

Common troubleshooting SQL:

```sql
SELECT status, count(*)
FROM onestep_executions
WHERE namespace = 'agent-api'
GROUP BY status
ORDER BY status;

SELECT id, task_name, status, attempts, worker_id,
       lease_expires_at, created_at, updated_at
FROM onestep_executions
WHERE namespace = 'agent-api'
ORDER BY created_at DESC
LIMIT 50;

SELECT execution_id, attempt_no, worker_id, status,
       started_at, heartbeat_at, finished_at
FROM onestep_execution_attempts
WHERE execution_id = '<execution-id>'
ORDER BY attempt_no;
```

Key alerts:

- `queued` growing for a long time: worker not started, task name/namespace mismatch, or database unavailable.
- `running` not decreasing: handler blocked, heartbeat not running, or worker crashed.
- `retrying` growing: business failure rate or downstream dependency anomalies.
- `cancel_requested` lingering: worker not heartbeating or unable to complete cancellation.
- `expired` growing: business `expires_at` too short or insufficient worker claim capacity.

## 11. Go-Live Checklist

Confirm in order before going live:

- [ ] Both `onestep==1.9.0` and `onestep-postgres==0.2.0` exist on PyPI.
- [ ] `pip check` passes on API and worker; both processes use the same version combination.
- [ ] Both API and worker print and verify the actual versions of `onestep` and `onestep-postgres`.
- [ ] As migration role, complete initialization of both execution tables.
- [ ] Both API and worker use `auto_create=False`.
- [ ] DSN, namespace, and table names are consistent between API and worker.
- [ ] If using a non-`public` schema, `search_path` is consistent for migration and runtime connections.
- [ ] Each task uses a separate `PostgresExecutionSource`, task names match exactly.
- [ ] Each worker instance has a unique `worker_id`; host clocks are synchronized.
- [ ] Handler database writes, message sending, and file writes have idempotency protection.
- [ ] Verified: success, failure with retry, cancellation, duplicate submission, and worker restart recovery.
- [ ] Alerts configured for queued/running/retrying/cancel_requested.

Minimum smoke test:

1. Submit a short task, verify state transitions from `queued` to `running` to `succeeded`.
2. Resubmit with the same request ID, verify the same execution ID is returned.
3. Submit different payload with the same request ID, verify API returns 409.
4. Submit a cancellable long task, verify final state is `cancelled`.
5. Restart worker during task execution, verify new worker can reclaim and produce a new attempt.
6. Paginate two pages, verify `next_cursor` is not duplicated and no items are missing.
7. Submit over-limit or non-encodable results, verify monitoring can detect and not falsely report success.

## 12. Rollback

If the new execution backend has issues:

1. Stop the API from submitting new tracked executions first.
2. Wait for or manually handle current `running`, `cancel_requested`, and `retrying` records.
3. Roll back API and worker together to compatible core/plugin versions, e.g., `onestep==1.8.1` with `onestep-postgres==0.1.3`.
4. Keep the `onestep_executions` and `onestep_execution_attempts` tables; do not drop them directly. They contain audit and recovery information.
5. When restoring to a newer version, run the smoke test first, then re-enable business submissions.

Old version workers will not process tasks from the new execution table, so do not allow new executions to enter the database during rollback unless a corresponding new-version worker is ready.

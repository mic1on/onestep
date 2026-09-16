---
title: MySQL Tracked Execution
outline: deep
---

# MySQL Tracked Execution

This document explains how to use MySQL as a submission, state, result, cancellation, and lease store for long-running tasks. The capability is provided by core `onestep>=1.9.0` plus `onestep-sql[mysql]>=0.4.0`; both are published on PyPI. It mirrors [PostgreSQL Tracked Execution](/en/broker/postgres-execution) point for point: the same `ExecutionClient` API, the same state machine, and the same YAML field semantics — only the database and driver layers are replaced.

> Start with [How Tracked Execution Works](/en/broker/execution-overview) for the roles and the end-to-end flow of one task; this page focuses on MySQL deployment details.

Use cases: An HTTP request submits a task that may run for seconds, minutes, or longer. The API returns a task ID, and the business side polls for status or results. Typical examples include Agents, report generation, file processing, async imports, and batch syncs.

This feature is optional. Plain `MemoryQueue`, RabbitMQ, Redis, SQS, scheduled tasks, and existing MySQL table queue integrations require no changes.

## 0. Confirm Version Requirements

| Business Scenario | Required Versions | Business Code Changes Needed |
| --- | --- | --- |
| Continue using regular queue, schedule, webhook | `onestep>=1.9.0` | No |
| Continue using existing MySQL table queue, incremental, binlog, state or sink | `onestep>=1.9.0` + compatible MySQL plugin | Usually no |
| Use submission, query, result, and cancellation from this page | `onestep>=1.9.0` + `onestep-sql[mysql]>=0.4.0` | Deploy API and worker per this page |

`onestep-sql[mysql]>=0.4.0` depends on `onestep>=1.9.0` and cannot be combined with `onestep==1.8.1`. Conversely, installing `onestep>=1.9.0` alone does not automatically enable tracked execution; workers without the SQL plugin installed can run normally.

## 1. Runtime Architecture

The API process handles submission, queries, and cancellation; the OneStep worker process claims and executes. Both processes collaborate through the same MySQL database. Do not start a OneStep worker inside a FastAPI or Django process.

```text
Business API                         OneStep worker
POST /executions                     MySQLExecutionSource
GET  /executions/{id}                OneStepApp + handler
POST /executions/{id}/cancel         heartbeat + lease completion
        |                                      |
        +------------- MySQL ------------------+
                 executions + attempts
```

Core object responsibilities:

| Object | Process | Purpose |
| --- | --- | --- |
| `ExecutionClient` | API | Submit, query, paginate, cancel, read results |
| `MySQLExecutionBackend` | API, advanced shared pool scenarios | Connect to the same execution tables |
| `MySQLExecutionSource` | worker | Claim tasks by namespace and task name |
| `OneStepApp` | worker | Schedule handler, retry, cancellation, and shutdown |

Each `MySQLExecutionSource` can only bind to one task name. To execute multiple tasks, create a separate source for each task.

## 2. Installation and Rollout

API and worker processes participating in the same execution chain must use the same locked versions:

```bash
pip install "onestep>=1.9.0" "onestep-sql[mysql]>=0.4.0"
```

When using uv:

```bash
uv add "onestep>=1.9.0" "onestep-sql[mysql]>=0.4.0"
uv run python -c "import onestep, onestep_sql.mysql; print(onestep.__version__, onestep_sql.mysql.__version__)"
uv run pip check
```

> `onestep-sql` is the canonical distribution package for MySQL and PostgreSQL (issue #133). The legacy `onestep-mysql` still works as a forwarding shim, but new deployments should use `onestep-sql[mysql]`. The Python import path `from onestep_mysql import ...` remains compatible.

### 2.1 Driver and Authentication

The DSN uses a SQLAlchemy MySQL URL (both `mysql+pymysql://...` and `mysql+asyncmy://...` work); the engine internally maps all of them to the asyncio driver `asyncmy`.

The default MySQL 8.x authentication plugin is `caching_sha2_password`; the **first** authentication over plaintext TCP requires the `cryptography` package. `onestep-sql[mysql]` (and `[all]`) already declares `cryptography>=41.0.0` explicitly, so after `pip install` / `uv sync` you can connect directly with no extra configuration. Do not rely on incidental conditions such as "the auth cache has been warmed by another connection" — a newly created user with a cold cache must be able to authenticate successfully right away.

Version requirement: **MySQL 8.0.16 or later** (the execution tables use CHECK constraints). Both the 8.0 and 8.4 release lines have been tested in practice against every dialect clause and behave identically.

## 3. Database Initialization

The execution backend uses two tables:

- `onestep_executions`: Task main record, state, payload, result, error, and current lease.
- `onestep_execution_attempts`: One attempt per claim, recording worker, heartbeats, and terminal state.

Production should create tables via a migration role and use `auto_create=False` at runtime. `auto_create` provides SQLAlchemy create-only initialization; it does not perform safe column changes or version migrations on existing tables with the same name.

Key points of the MySQL implementation:

- Tables use `InnoDB` + `utf8mb4`; time columns are `DATETIME(6)` (microsecond precision, so `available_at` is never rounded up).
- payload / metadata / result are `JSON` columns; defaults use expression defaults.
- Concurrent `auto_create` serializes the table-creation DDL for the same table pair via `GET_LOCK`, so multiple workers starting at the same time will not hit error 1050 (table already exists).
- CHECK / foreign key / index names are derived from the table names and globally unique within the schema. Therefore **multiple execution table sets can coexist in the same database** (just use different table name combinations), but do not make `attempts_table` too long (keep it within about 58 characters), otherwise the derived constraint names will exceed MySQL's 64-character identifier limit.
- Engine sessions are pinned to the UTC time zone + `READ COMMITTED`. This is an engine-level setting of the execution backend, independent of session parameters configured on the connector; for workloads that need different session settings, create a separate `MySQLConnector` for them.

### 3.1 One-Time Initialization Script

Execute once during deployment using a separate connection with DDL privileges:

```python
# deploy/create_execution_tables.py
import asyncio
import os

from onestep_sql.mysql import MySQLExecutionBackend


async def main() -> None:
    backend = MySQLExecutionBackend(
        dsn=os.environ["MYSQL_EXECUTION_MIGRATION_DSN"],
        table=os.getenv("MYSQL_EXECUTIONS_TABLE", "onestep_executions"),
        attempts_table=os.getenv(
            "MYSQL_EXECUTION_ATTEMPTS_TABLE",
            "onestep_execution_attempts",
        ),
        auto_create=True,
    )
    await backend.open()
    await backend.close()


asyncio.run(main())
```

After successful execution, both API and worker use `auto_create=False`. If custom table names are used, the initialization script, API, and worker must be fully consistent.

`table` and `attempts_table` only accept SQL identifiers without schema qualifiers, e.g., `onestep_executions`, not `app.onestep_executions`. For cross-database (multi-tenant sharding) deployments, run the same initialization script in each database.

Note: for combinations where multiple execution table sets **share the same `executions` table but use different `attempts` tables**, do not run `open()` concurrently (initialize one set first, then open the next); concurrent table creation for the same table pair is safe, and sequential initialization of arbitrary table pairs is also safe.

### 3.2 Runtime Database Permissions

The runtime identity should not have DDL permissions. An execution-only scenario needs at least SELECT, INSERT, and UPDATE permissions on both tables:

```sql
GRANT SELECT, INSERT, UPDATE
ON onestep_executions TO 'onestep_runtime'@'%';
GRANT SELECT, INSERT, UPDATE
ON onestep_execution_attempts TO 'onestep_runtime'@'%';
```

Pre-production check:

```sql
SHOW CREATE TABLE onestep_executions;
SHOW CREATE TABLE onestep_execution_attempts;
```

If the tables already exist but from another version, do not simply set `auto_create=False` and continue. First use the migration role to verify columns, constraints, and indexes.

## 4. Shared Configuration

API and worker share at least the following configuration:

```bash
MYSQL_EXECUTION_DSN=mysql+pymysql://app_runtime:***@db.example.com:3306/app
MYSQL_EXECUTION_NAMESPACE=agent-api
MYSQL_EXECUTIONS_TABLE=onestep_executions
MYSQL_EXECUTION_ATTEMPTS_TABLE=onestep_execution_attempts
```

Do not write DSN, passwords, or tokens into code, YAML plaintext, or logs. `MySQLConnector` provides redacted tokens to connector errors, but business logs should still avoid printing DSNs.

The namespace is a business isolation boundary. API and worker must use the same namespace; other businesses can share the same database using different namespaces. The task name is a routing key; the task name at submission must exactly match the worker source's task name.

The namespace is a logical routing and query boundary, not a database permission boundary. Tenants requiring strong isolation should use separate databases, accounts, or authorization at the business API layer, not rely solely on namespace strings.

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
from onestep_sql.mysql import MySQLExecutionBackend
from pydantic import BaseModel, Field


backend = MySQLExecutionBackend(
    dsn=os.environ["MYSQL_EXECUTION_DSN"],
    table=os.getenv("MYSQL_EXECUTIONS_TABLE", "onestep_executions"),
    attempts_table=os.getenv(
        "MYSQL_EXECUTION_ATTEMPTS_TABLE",
        "onestep_execution_attempts",
    ),
    auto_create=False,
)
executions = ExecutionClient(
    backend,
    namespace=os.getenv("MYSQL_EXECUTION_NAMESPACE", "agent-api"),
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

The `ExecutionClient` is exactly the same for both backends: switching from `PostgresExecutionBackend` to `MySQLExecutionBackend` only requires changing the line that constructs the backend.

### 5.1 Submit Requests and Idempotency

The business API should use a stable business request ID as `idempotency_key`. Resubmitting the same namespace, task name, and idempotency key with identical content returns the original execution; if payload, metadata, or other submission parameters differ, `ExecutionConflict` is raised, typically mapped to HTTP 409.

Network-facing requests should not omit the `Idempotency-Key` header; do not expose arbitrary task names to external callers — use an allowlist (as in the example above). Authentication info such as `tenant_id` and `requested_by` should be injected by the server.

### 5.2 Time Semantics (MySQL-Specific)

- **`expires_at` must include a timezone**; naive datetimes are rejected (422 at the submission endpoint, `ValueError` at the backend layer).
- MySQL's `DATETIME` binding parameters **drop the offset instead of converting time zones**: binding `12:00+08:00` directly stores `12:00`, which reads back 8 hours off as UTC — tasks that have already expired could be misjudged as claimable. The backend normalizes all aware datetimes **to UTC** at the write boundary before storing; the business side does not need to convert anything itself, but do not rely on "the stored value equaling local wall-clock time".
- `DATETIME(6)` preserves microsecond precision: a task submitted with `delay_s=0` is claimable immediately, and `available_at` is never rounded up.
- Lease expiry is judged using a clock injected by the backend (the process UTC clock), not MySQL's `NOW()` — time inside a MySQL transaction is not stable and cannot serve as a unified `now`.

### 5.3 Data Types and Size Limits

Default limits are calculated on encoded JSON size: payload 1 MiB, metadata 64 KiB, result 1 MiB. Large files, model contexts, and binary artifacts should be stored in object storage first, submitting only URIs, checksums, and necessary metadata to execution.

The metadata key `onestep.execution` is reserved by the runtime and cannot be used by business submissions. Handler return values must also satisfy the same encoding constraints; test with the largest result sample before going live.

### 5.4 Business Call Flow

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

**`result()` does not poll or wait**: when the task has no terminal state yet, it raises `ExecutionNotReady` immediately (corresponding to HTTP 409/202), and the caller decides when to ask again. The waiting logic always lives on the business side.

## 6. Worker Process

```python
# app/worker.py
import os
from typing import Any

from onestep import ExponentialBackoff, OneStepApp
from onestep_sql.mysql import MySQLExecutionSource


app = OneStepApp("agent-worker", shutdown_timeout_s=30.0)
jobs = MySQLExecutionSource(
    dsn=os.environ["MYSQL_EXECUTION_DSN"],
    table=os.getenv("MYSQL_EXECUTIONS_TABLE", "onestep_executions"),
    attempts_table=os.getenv(
        "MYSQL_EXECUTION_ATTEMPTS_TABLE",
        "onestep_execution_attempts",
    ),
    auto_create=False,
    reclaim_batch_size=100,
    namespace=os.getenv("MYSQL_EXECUTION_NAMESPACE", "agent-api"),
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

The handler return value is written to the execution's `result` by the managed runtime. Business handlers do not need to — and should not — manually call `ack()`, `retry()`, or `fail()`.

The `MySQLExecutionSource` constructor parameter set aligns parameter-for-parameter with `PostgresExecutionSource`; `backend.source(...)` and `MySQLExecutionSource(...)` are equivalent forms. To share a `MySQLConnector` with table queue, sink, or state store, use `MySQLExecutionSource.from_connector(mysql, ...)` or create `MySQLExecutionBackend.from_connector(mysql, ...)` first; the shared connector is still closed by the caller. Note that the execution engine's session settings (UTC + `READ COMMITTED`) apply to the whole engine, and **business connections already established on the reused connector before the backend is built are not retroactively adjusted**; creating a dedicated connector for execution is recommended.

If the handler is a synchronous blocking function, use `asyncio.to_thread()` or other thread pool isolation to ensure the heartbeat task can keep running. `heartbeat_interval_s` must satisfy:

```text
0 < heartbeat_interval_s <= lease_duration_s / 3
```

Multiple worker replicas can use the same source configuration, but `worker_id` should use pod name, hostname, or another instance-unique identifier for lease and attempt diagnosis.

## 7. YAML Worker Configuration

If the worker uses YAML, the API still uses Python `ExecutionClient`. YAML only handles worker wiring, not HTTP API.

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: agent-worker

resources:
  db:
    type: mysql
    dsn: "${MYSQL_EXECUTION_DSN}"

  agent_jobs:
    type: mysql_execution_source
    connector: db
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

The `mysql_execution_source` strict validation mirrors `postgres_execution_source`: namespace non-empty and ≤255; exactly one `task_names` entry; `batch_size ≥ 1`; `heartbeat_interval_s ≤ lease_duration_s / 3`. **The connector must be a MySQL connector** — passing a PostgreSQL connector to `mysql_execution_source` (or the reverse) fails at load time; tracked execution is implemented separately per backend and is never shared across backends.

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

The system is **at-least-once**, not exactly-once. The following scenarios may cause the handler or external side effects to execute again:

- Worker crashes after external writes but before completing the execution;
- Lease expires and another worker takes over (the old token is fenced and heartbeats report `StaleExecutionLease`);
- Database connection drops during result commit, and the business side cannot determine if the commit succeeded;
- Handler enters the next attempt per retry policy.

**External side effects must still be deduplicated using `execution_id` (or a business idempotency key) as the idempotency key**, for example:

```sql
CREATE UNIQUE INDEX uq_business_result_request
ON business_results (request_id);
```

Do not treat "result not found" as "the task definitely did not execute". If the API connection drops after submission, retry with the same `idempotency_key` instead of generating a new request ID.

Recommended lease parameters:

| Parameter | Default | Recommendation |
| --- | --- | --- |
| `lease_duration_s` | 90 | Adjust based on the longest normal database jitter and heartbeat latency |
| `heartbeat_interval_s` | 30 | No more than one third of lease duration |
| `reclaim_batch_size` | 100 | Adjust based on database load and recovery speed |
| `batch_size` | 100 | Should generally not be many times larger than worker concurrency |
| `poll_interval_s` | 1 | Affects claim latency when idle |

Two workers claiming concurrently will not obtain the same valid lease token (claim uses `FOR UPDATE SKIP LOCKED`-style atomic claiming + a lease CAS); after a lease expires, another worker can take over. Claiming over an empty range does not block concurrent submissions with `REPEATABLE-READ` gap locks — the execution engine pins `READ COMMITTED`, so empty claims do not block concurrent submissions.

Expired executions and stuck leases are recovered by the next `claim()` — there is no independent reaper. When all workers are stopped, no reclaim happens; after workers resume, backlogs are processed in batches of `reclaim_batch_size`.

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
- `running` not decreasing for a long time: handler blocked, heartbeat not running, or worker crashed.
- `retrying` growing: business failure rate or downstream dependency anomalies.
- `cancel_requested` lingering: worker not heartbeating or unable to complete cancellation.
- `expired` growing: business `expires_at` too short or insufficient worker claim capacity.

## 11. Go-Live Checklist

Confirm in order before going live:

- [ ] `onestep>=1.9.0` and `onestep-sql>=0.4.0` (with the MySQL backend) are both published on PyPI, and the target version combination resolves and installs.
- [ ] `pip check` passes on API and worker; both processes use the same version combination.
- [ ] MySQL version ≥ 8.0.16 (CHECK constraints); both 8.0 and 8.4 are within the supported range.
- [ ] When the runtime account uses `caching_sha2_password`, the installation environment includes the `cryptography` dependency declared by `onestep-sql[mysql]` (`python -c "import cryptography"` succeeds after `uv sync` / `pip install`).
- [ ] Initialization of the two execution tables is completed as the migration role (or `auto_create` at worker startup is confirmed to be concurrency-safe).
- [ ] Both API and worker use `auto_create=False` (recommended for production).
- [ ] DSN, namespace, and table names are consistent between API and worker.
- [ ] Each task uses a separate `MySQLExecutionSource`; task names match exactly.
- [ ] Each worker instance has a unique `worker_id`.
- [ ] Handler database writes, message sending, and file writes have idempotency protection (deduplicated by `execution_id` or business idempotency key).
- [ ] Verified: success, failure with retry, cancellation, duplicate submission, and worker restart recovery.
- [ ] Alerts configured for queued/running/retrying/cancel_requested.

Minimum smoke test:

1. Submit a short task, verify state transitions from `queued` to `running` to `succeeded`.
2. Resubmit with the same request ID, verify the same execution ID is returned.
3. Submit different payload with the same request ID, verify API returns 409.
4. Submit a cancellable long task, verify final state is `cancelled`.
5. Restart worker during task execution, verify the new worker can reclaim and produce a new attempt.
6. Paginate two pages, verify `next_cursor` is not duplicated and no items are missing.

## 12. Rollback

If the new execution backend has issues:

1. Stop the API from submitting new tracked executions first.
2. Wait for or manually handle current `running`, `cancel_requested`, and `retrying` records.
3. Roll back API and worker together to compatible core/plugin versions.
4. Keep the `onestep_executions` and `onestep_execution_attempts` tables; do not drop them directly. They contain audit and recovery information.
5. When restoring to a newer version, run the smoke test first, then re-enable business submissions.

Old version workers will not process tasks in the execution tables, so do not allow new executions to enter the database during rollback unless a corresponding new-version worker has been prepared.

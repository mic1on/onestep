# Web Service Integration

This guide covers the recommended deployment model when a project uses
`onestep` alongside a web framework such as FastAPI or Django.

## Recommendation

Run the web app and `OneStepApp` as separate processes.

- Web service: handles HTTP, auth, validation, and request/response lifecycle
- OneStep worker: runs `onestep run package.module:app`
- Shared code: business logic, schemas, settings, and queue publishing helpers

This is the default recommendation because:

- the official deployment entrypoint is the `onestep` CLI
- `OneStepApp.serve()` is a long-running worker loop
- web servers often use multiple worker processes and reload modes
- embedded startup can accidentally start duplicate workers
- `WebhookSource` opens its own listening socket and should not compete with an
  existing web server unless that is the explicit design

## Recommended architecture

Use a durable backend between the web app and OneStep worker.

- HTTP-triggered jobs: web route validates input, then publishes to RabbitMQ,
  SQS, or a MySQL-backed queue consumed by OneStep
- Scheduled jobs: OneStep owns `IntervalSource` or `CronSource` in its own
  worker process
- Webhook ingestion in an existing web app: handle the webhook in FastAPI or
  Django, then publish to the backend queue; do not add `WebhookSource`
  unless OneStep itself should own the public webhook endpoint

## Suggested module layout

```text
your_project/
  api.py
  onestep_app.py
  queueing.py
  settings.py
  tasks/
```

- `api.py`: FastAPI or Django HTTP entrypoint
- `onestep_app.py`: defines the `OneStepApp`
- `queueing.py`: shared helpers for publishing messages to the durable backend
- `settings.py`: shared environment loading and configuration

## FastAPI example

```python
# your_project/onestep_app.py
from onestep import OneStepApp
from onestep_rabbitmq import RabbitMQConnector

app = OneStepApp("billing-sync")
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
jobs = rmq.queue("billing.jobs")


@app.task(source=jobs)
async def sync_billing(ctx, payload):
    ...
```

```python
# your_project/api.py
from fastapi import FastAPI

from .queueing import publish_billing_job

api = FastAPI()


@api.post("/billing/sync")
async def trigger_sync(payload: dict):
    await publish_billing_job(payload)
    return {"accepted": True}
```

Run them as separate services:

```bash
uvicorn your_project.api:api --host 0.0.0.0 --port 8000
onestep run your_project.onestep_app:app
```

## Tracked HTTP tasks with PostgreSQL

For long-running HTTP-triggered tasks that need a durable status and result
record, use the PostgreSQL execution backend. Keep the API process and worker
process separate:

```text
FastAPI process                         OneStep worker process
POST /agent-runs                        PostgresExecutionSource
        |                                        |
        +---- ExecutionClient + PostgreSQL -------+
                 executions + attempts
```

The API process owns submission and queries:

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from onestep import ExecutionClient
from onestep_postgres import PostgresExecutionBackend

backend = PostgresExecutionBackend(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=False,
)
step = ExecutionClient(backend, namespace="agent-api")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    async with step:
        yield


api = FastAPI(lifespan=lifespan)


@api.post("/agent-runs", status_code=202)
async def submit_agent_run(request: dict):
    execution = await step.submit(
        "run_agent",
        request["payload"],
        idempotency_key=request.get("request_id"),
    )
    return {"task_id": str(execution.id), "status": execution.status.value}


@api.get("/agent-runs/{task_id}")
async def get_agent_run(task_id):
    execution = await step.get(task_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="task not found")
    return execution


@api.post("/agent-runs/{task_id}/cancel")
async def cancel_agent_run(task_id):
    execution = await step.cancel(task_id, reason="requested by user")
    if execution is None:
        raise HTTPException(status_code=404, detail="task not found")
    return execution


@api.get("/agent-runs/{task_id}/result")
async def get_agent_result(task_id):
    try:
        return {"result": await step.result(task_id)}
    except Exception as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
```

The separate worker registers the same execution table and only claims task
names it can handle:

```python
from onestep import OneStepApp
from onestep_postgres import PostgresExecutionSource

app = OneStepApp("agent-worker")
jobs = PostgresExecutionSource(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=False,
    namespace="agent-api",
    task_names=("run_agent",),
    worker_id="agent-worker-1",
)


@app.task(source=jobs, concurrency=4)
async def run_agent(ctx, payload):
    return await run_agent_model(payload)
```

Execution records are at-least-once. A handler or external side effect can be
run again after a worker crash, so use the execution ID or an idempotency key
when downstream writes must converge. `cancel()` is cooperative: queued work
is cancelled immediately, while running work is asked to stop through the
heartbeat. It cannot undo a handler that blocks cancellation or has already
performed an external side effect. Keep `auto_create=False` in production and
create the execution tables during deployment with a migration role.

## Django example

Use Django views, DRF endpoints, signals, or model save hooks to publish work to
the shared backend, and run OneStep separately.

```python
# your_project/onestep_app.py
from onestep import OneStepApp
from onestep_rabbitmq import RabbitMQConnector

app = OneStepApp("billing-sync")
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
jobs = rmq.queue("billing.jobs")


@app.task(source=jobs)
async def sync_billing(ctx, payload):
    ...
```

Do not start OneStep from `AppConfig.ready()`. Django can execute that code
multiple times across worker processes, reloads, management commands, and test
runs.

Run them as separate services:

```bash
gunicorn your_project.wsgi:application
onestep run your_project.onestep_app:app
```

If you use ASGI Django, the same rule applies:

```bash
uvicorn your_project.asgi:application
onestep run your_project.onestep_app:app
```

## systemd shape

For production, create two units:

- one unit for the web app
- one unit for the OneStep worker

This directory already includes an example worker unit:

- `systemd/onestep-app.service`

Reuse that pattern for the OneStep worker and keep the web service managed by
its own unit.

## Embedded mode

Embedded startup is acceptable only for local development or carefully
controlled single-process deployments.

FastAPI example:

```python
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .onestep_app import app as worker_app


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(worker_app.serve())
    try:
        yield
    finally:
        worker_app.request_shutdown()
        await task


api = FastAPI(lifespan=lifespan)
```

Only use this when all of the following are true:

- exactly one web process will run
- auto-reload is disabled
- duplicate worker execution is acceptable or explicitly prevented

Do not use embedded startup as the default production pattern.

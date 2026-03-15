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
from onestep import OneStepApp, RabbitMQConnector

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

## Django example

Use Django views, DRF endpoints, signals, or model save hooks to publish work to
the shared backend, and run OneStep separately.

```python
# your_project/onestep_app.py
from onestep import OneStepApp, RabbitMQConnector

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

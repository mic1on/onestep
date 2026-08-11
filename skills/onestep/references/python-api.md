# Python API

Use this reference when writing Python-first onestep apps or handler code.

## App

```python
from onestep import OneStepApp

app = OneStepApp(
    "worker-name",
    config={"env": "local"},
    shutdown_timeout_s=30.0,
)
```

Handlers can access `ctx.config`, `ctx.task_config`, and `ctx.resources`.

## Task

```python
@app.task(
    name="sync_users",
    description="Sync users into the warehouse.",
    source=source,
    emit=[sink],
    dead_letter=dead_letter_sink,
    config={"target_table": "dw_users"},
    concurrency=4,
    retry=retry_policy,
    timeout_s=120,
)
async def sync_users(ctx, item):
    ...
```

Keep task definitions minimal. Omit optional fields until needed.

## Sources, Sinks, Deliveries

`Source.fetch(limit)` returns deliveries. Each delivery exposes:

- `delivery.payload`
- `await delivery.ack()`
- `await delivery.retry(delay_s=...)`
- `await delivery.fail(exc)`

`Sink.send(envelope)` publishes a full `Envelope`. `Sink.publish(body, meta=None, attempts=0)` is available for simple sends.

## Schedules

```python
from onestep import CronSource, IntervalSource

every_five_minutes = IntervalSource.every(
    minutes=5,
    immediate=True,
    overlap="skip",
)

daily = CronSource(
    "0 2 * * *",
    timezone="Asia/Shanghai",
    overlap="skip",
    max_queued_runs=1000,
)
```

`overlap` is one of `allow`, `skip`, or `queue`.
`max_queued_runs` bounds the backlog retained by `queue` mode.

## Hooks

```python
@app.on_startup
async def startup(app):
    ...


@app.on_shutdown
async def shutdown(app):
    ...


@app.on_event
async def observe(app, event):
    ...
```

Use hooks sparingly. Prefer handler-local code for task-specific behavior.

## Resources

```python
queue = app.register_resource("incoming", MemoryQueue("incoming"))

@app.task(source=queue)
async def consume(ctx, item):
    same_queue = ctx.resources["incoming"]
```

For YAML apps, resources are bound from the YAML resource registry.

## Execution client

Use `ExecutionClient` when an API process needs to submit and query a durable
long-running task through a plugin backend:

```python
from onestep import ExecutionClient

step = ExecutionClient(backend, namespace="agent-api")
async with step:
    execution = await step.submit(
        "run_agent",
        payload,
        idempotency_key=request_id,
        metadata={"requested_by": user_id},
    )
    current = await step.get(execution.id)
    page = await step.list(task_name="run_agent", limit=50)
    cancelled = await step.cancel(execution.id, reason="user requested cancellation")
    result = await step.result(execution.id)
```

`Execution` values are immutable snapshots and never auto-refresh. `result()`
does not poll or wait: it returns a successful result, or raises
`ExecutionNotReady`, `ExecutionFailed`, `ExecutionCancelled`,
`ExecutionExpired`, or `ExecutionNotFound`. Use an idempotency key for repeated
HTTP requests, and treat handler side effects as at-least-once.

## Manual Run And Control Commands

Some sources support manual runs. The app exposes runtime control helpers such as `run_task_once`, drain, pause/resume, and dead-letter replay/discard. Use these when integrating with the control plane or operational tooling; do not add them to simple workers by default.

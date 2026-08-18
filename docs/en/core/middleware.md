---
title: Middleware | Core
outline: deep
---

# Middleware

onestep 1.x uses **event hooks** instead of the traditional middleware pattern, providing clearer lifecycle control.

## Event Hooks

When started with `onestep run`, the CLI registers `StructuredEventLogger` by default; applications do not need to configure it again. Embedded applications that call `app.run()` or `app.serve()` directly are still managed by the host process for logging and event handlers.

Register custom event handlers with `@app.on_event`:

```python
from onestep import InMemoryMetrics, OneStepApp

app = OneStepApp("demo")
metrics = InMemoryMetrics()

# Register metric handlers that need to be held by the app
app.on_event(metrics)


@app.task(source=...)
async def my_task(ctx, item):
    return item
```

## Execution Events

The following events are emitted during task execution:

| Event | Trigger |
|-------|--------|
| `fetched` | Message fetched from Source |
| `started` | Task execution started |
| `succeeded` | Task execution succeeded |
| `retried` | Task retried |
| `failed` | Task ultimately failed |
| `dead_lettered` | Message entered dead letter queue |
| `cancelled` | Task cancelled |

## Custom Event Handler

Implement a custom event handler:

```python
from onestep import TaskEvent, TaskEventKind


@app.on_event
def log_event(event: TaskEvent):
    if event.kind is TaskEventKind.SUCCEEDED:
        print(f"Task succeeded: {event.task}, duration: {event.duration_s:.2f}s")
    elif event.kind is TaskEventKind.FAILED and event.failure is not None:
        print(f"Task failed: {event.task}, reason: {event.failure.message}")
```

## Built-in Event Handlers

### InMemoryMetrics

In-memory metrics collector:

```python
from onestep import InMemoryMetrics

metrics = InMemoryMetrics()
app.on_event(metrics)

# Get metrics snapshot
snapshot = metrics.snapshot()
print(snapshot["kinds"])  # Event type counts
```

### StructuredEventLogger

`onestep run` enables structured task events by default. For embedded runs or when custom event logging is needed, enable it explicitly:

```python
import logging

from onestep import StructuredEventLogger

app.on_event(
    StructuredEventLogger(logger=logging.getLogger("billing.task_events"))
)
```

Output fields: `event_kind`, `app_name`, `task_name`, `source_name`, `attempts`, `duration_s`, `failure_kind`

You can also use `app.enable_structured_event_logging()` to idempotently enable the default handler; if a `StructuredEventLogger` already exists, it reuses it. See [Logging & Task Events](/guide/logging) for CLI parameters and log level rules.

## Lifecycle Hooks

### @app.on_startup

Executes when the application starts:

```python
@app.on_startup
async def bootstrap(app):
    print("Application started")
    # Initialize resources, pre-publish messages, etc.
```

### @app.on_shutdown

Executes when the application shuts down:

```python
@app.on_shutdown
async def cleanup(app):
    print("Application shutting down")
    # Clean up resources
```

## Task State

Each task can maintain independent state:

```python
from onestep import InMemoryStateStore, OneStepApp

app = OneStepApp("state-demo", state=InMemoryStateStore())


@app.task(source=...)
async def track_runs(ctx, item):
    # Get state
    runs = await ctx.state.get("runs", 0)
    
    # Update state
    await ctx.state.set("runs", runs + 1)
    
    print(f"Processed {runs + 1} messages")
```

## Config Access

Access application config via `ctx.config`:

```python
app = OneStepApp("demo", config={"region": "cn", "debug": True})


@app.task(source=...)
async def my_task(ctx, item):
    region = ctx.config["region"]
    debug = ctx.config.get("debug", False)
    ...
```

## Migrating from 0.5.x

Old middleware:

```python
# 0.5.x
class MyMiddleware(BaseMiddleware):
    def before_consume(self, step, message, *args, **kwargs):
        ...

@step(from_broker=..., middlewares=[MyMiddleware()])
def task(message):
    ...
```

New event hooks:

```python
# 1.x
@app.on_event
def log_event(event):
    if event.kind.value == "started":
        # Equivalent to before_consume
        ...
    elif event.kind.value == "succeeded":
        # Equivalent to after_consume
        ...

@app.task(source=...)
async def task(ctx, item):
    ...
```

Message deduplication should now be implemented in the task handler, or using database unique constraints.

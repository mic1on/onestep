---
title: Core Concepts | Core
outline: deep
---

# Core Concepts

## Architecture Overview

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Source    │ ──► │    Task     │ ──► │    Sink     │
│  (Data In)  │     │ (Process)   │     │  (Data Out) │
└─────────────┘     └─────────────┘     └─────────────┘
```

## OneStepApp

Application entry point, responsible for task registration and lifecycle management:

```python
from onestep import OneStepApp

app = OneStepApp(
    "my-app",                    # Application name
    config={"key": "value"},     # Configuration
    state=InMemoryStateStore(),  # State store
    shutdown_timeout_s=30.0,     # Shutdown timeout
)
```

### Task Registration

```python
@app.task(source=..., emit=...)
async def my_task(ctx, item):
    ...
```

### Event Listening

```python
app.on_event(InMemoryMetrics())
app.on_event(StructuredEventLogger())
```

### Lifecycle Hooks

```python
@app.on_startup
async def bootstrap(app):
    ...

@app.on_shutdown
async def cleanup(app):
    ...
```

## Source

Data input source, responsible for fetching messages:

```python
from onestep import CronSource, IntervalSource, MemoryQueue, WebhookSource
from onestep_mysql import MySQLConnector
from onestep_rabbitmq import RabbitMQConnector

# In-memory queue
source = MemoryQueue("incoming")

# Timer
source = IntervalSource.every(minutes=5)

# Cron
source = CronSource("0 * * * *")

# Webhook
source = WebhookSource(path="/webhook")

# RabbitMQ
source = RabbitMQConnector("amqp://...").queue("jobs")

# MySQL
source = MySQLConnector("mysql://...").table_queue("tasks")
```

RabbitMQ, MySQL, Redis Streams, AWS SQS, and Feishu Bitable are provided by plugin packages. Install them and import from the corresponding plugin module.

### Custom Source

```python
from onestep import Source, Delivery

class MySource(Source):
    async def fetch(self) -> list[Delivery]:
        # Fetch messages
        ...
    
    async def ack(self, delivery: Delivery):
        # Acknowledge message
        ...
```

## Sink

Data output target, responsible for publishing messages:

```python
from onestep import MemoryQueue
from onestep_mysql import MySQLConnector
from onestep_rabbitmq import RabbitMQConnector

# In-memory queue
sink = MemoryQueue("output")

# RabbitMQ
sink = RabbitMQConnector("amqp://...").queue("results")

# MySQL
sink = MySQLConnector("mysql://...").table_sink("results")
```

### Custom Sink

```python
from onestep import Sink

class MySink(Sink):
    async def publish(self, body, meta=None):
        # Publish message
        ...
```

## Delivery

The message delivery object:

The runtime gets a `Delivery` from `Source.fetch()`, then passes `delivery.payload` to the task function. Custom Source implementations need to implement `ack()`, `retry()` and `fail()`; built-in connectors already handle acknowledgement, retry, and failure semantics.

## Task Context

Task execution context:

```python
@app.task(source=...)
async def my_task(ctx, item):
    # ctx.app - OneStepApp instance
    # ctx.config - Application config
    # ctx.state - Task state
    # ctx.current - Current execution info
    ...
```

### Config Access

```python
app = OneStepApp("demo", config={"region": "cn"})


@app.task(source=...)
async def task(ctx, item):
    region = ctx.config["region"]
```

### State Management

```python
@app.task(source=...)
async def task(ctx, item):
    count = await ctx.state.get("count", 0)
    await ctx.state.set("count", count + 1)
```

## Message Flow

### Basic Flow

```python
@app.task(source=source, emit=sink)
async def process(ctx, item):
    return {"result": item}  # Return value sent to sink
```

### Multi-Stage Flow

```python
queue1 = MemoryQueue("stage1")
queue2 = MemoryQueue("stage2")


@app.task(source=MemoryQueue("input"), emit=queue1)
async def stage1(ctx, item):
    return item * 2


@app.task(source=queue1, emit=queue2)
async def stage2(ctx, item):
    return item + 1


@app.task(source=queue2)
async def final(ctx, item):
    print(f"Result: {item}")
```

## Managed Execution

onestep 1.9 introduced Managed Execution mode, which persists task state, results, and leases to a database (currently PostgreSQL only), suitable for long-running tasks (such as AI Agent invocations).

### Architecture

```
FastAPI / Gateway                  Worker
┌──────────────┐                  ┌──────────────────┐
│ExecutionClient│  ──submit──►    │ExecutionBackend   │◄── PostgresExecutionSource
│  .submit()    │                  │  (PostgreSQL)     │     .claim()
│  .get()       │                  │                   │     heartbeat/complete
│  .cancel()    │                  └───────────────────┘
└──────────────┘
```

### Submitting an Execution

```python
from onestep import ExecutionClient
from onestep_postgres import PostgresExecutionBackend

backend = PostgresExecutionBackend(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=True,
)
client = ExecutionClient(backend, namespace="agent-api")

async with client:
    execution = await client.submit(
        "run_agent",
        {"prompt": "..."},
        idempotency_key=request_id,
    )
    # Poll for result
    result = await execution.result()
```

### Worker Consumption

```python
from onestep_postgres import PostgresExecutionSource

source = PostgresExecutionSource(
    dsn="postgresql+psycopg://app:secret@db/app",
    namespace="agent-api",
    task_names=("run_agent",),
    worker_id="agent-worker-1",
)
```

Each execution source can only be configured with one task name, which must match the app task name bound to that source.

### State Machine

Task states include `queued` → `running` → `succeeded` / `failed` / `cancelled` / `expired`, with intermediate states `retrying` and `cancel_requested`. `Execution` is an immutable snapshot; call `get()` or `list()` again to get the latest state.

### Leases & Reliability

Executions use leases to guarantee at-least-once delivery: workers renew via `heartbeat()`, and expired leases are reclaimed by `claim()`. Cancellation is cooperative; external side effects from handlers still require business-level idempotency.

See [PostgreSQL Tracked Execution](/broker/postgres-execution) and [Core Reliability](/core-reliability) for details.

## Error Handling

### Retry

```python
from onestep import MaxAttempts

@app.task(
    source=...,
    retry=MaxAttempts(max_attempts=3, delay_s=1.0)
)
async def might_fail(ctx, item):
    ...
```

### Dead Letter Queue

```python
@app.task(
    source=main_queue,
    dead_letter=dead_letter_queue
)
async def risky_task(ctx, item):
    ...
```

### Timeout

```python
@app.task(source=..., timeout_s=30.0)
async def long_task(ctx, item):
    ...
```

## Running Modes

### Direct Run

```python
if __name__ == "__main__":
    app.run()
```

### CLI Run

```bash
onestep run module:app
```

### Async Run

```python
import asyncio

async def main():
    await app.serve()

asyncio.run(main())
```

## Next Steps

- [Connector](/core/connector) - Connector details
- [Retry](/core/retry) - Retry strategies
- [Middleware](/core/middleware) - Event hooks

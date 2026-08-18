---
title: Features | Guide
outline: deep
---

# Features

## Core Features

### Simple and Intuitive

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("demo")


@app.task(source=IntervalSource.every(seconds=30))
async def my_task(ctx, item):
    print("processing:", item)
```

### Multiple Data Sources

| Type | Source | Sink | Description |
|------|--------|------|-------------|
| Memory Queue | Yes | Yes | Dev & testing |
| Timer | Yes | No | Cron/Interval |
| Webhook | Yes | No | HTTP ingestion |
| HTTP Sink | No | Yes | HTTP JSON output |
| RabbitMQ | Yes | Yes | Distributed queue |
| Redis Streams | Yes | Yes | Lightweight stream queue |
| AWS SQS | Yes | Yes | Cloud queue |
| MySQL | Yes | Yes | Table queue / Incremental sync / binlog CDC / table output |
| PostgreSQL | Yes | Yes | Table queue / Incremental sync / table output |
| Kafka | Yes | Yes | Topic consume & produce |
| Feishu Bitable | Yes | Yes | Bitable incremental sync / table output |
| Custom | Yes | Yes | Any data source |

### Flexible Data Flow

```python
from onestep import CronSource
from onestep_mysql import MySQLConnector
from onestep_rabbitmq import RabbitMQConnector

rmq = RabbitMQConnector("amqp://...")
db = MySQLConnector("mysql+pymysql://...")
results = rmq.queue("results")


@app.task(source=CronSource("0 * * * *"), emit=results)
async def scheduled_to_mq(ctx, _):
    return {"data": "..."}


@app.task(source=results, emit=db.table_sink(table="results"))
async def mq_to_db(ctx, item):
    return item
```

### Concurrency Control

```python
@app.task(source=..., concurrency=4)
async def task1(ctx, item):
    ...


@app.task(source=..., concurrency=64)
async def task2(ctx, item):
    ...
```

### Retry Mechanism

```python
from onestep import MaxAttempts


@app.task(
    source=...,
    retry=MaxAttempts(max_attempts=3, delay_s=1.0),
)
async def might_fail(ctx, item):
    ...
```

`max_attempts` includes the first execution. The configuration above means at most 2 additional attempts after the first failure.

### Dead Letter Queue

```python
@app.task(
    source=main_queue,
    dead_letter=dead_letter_queue,
    retry=MaxAttempts(max_attempts=3),
)
async def risky_task(ctx, item):
    ...


@app.task(source=dead_letter_queue)
async def handle_dead_letter(ctx, item):
    print(item["payload"])
    print(item["failure"])
```

### Execution Timeout

```python
@app.task(source=..., timeout_s=30.0)
async def long_task(ctx, item):
    await asyncio.sleep(60)
```

When `timeout_s` is exceeded, the runtime cancels the task and follows the failure flow.

### Event Listening

```python
from onestep import InMemoryMetrics, OneStepApp, StructuredEventLogger, TaskEventKind

app = OneStepApp("demo")
metrics = InMemoryMetrics()

app.on_event(metrics)
app.on_event(StructuredEventLogger())


@app.on_event
def log_success(event):
    if event.kind is TaskEventKind.SUCCEEDED:
        print(f"Succeeded: {event.task}")
```

### State Management

```python
from onestep import InMemoryStateStore, OneStepApp

app = OneStepApp("demo", state=InMemoryStateStore())


@app.task(source=...)
async def track_runs(ctx, item):
    runs = await ctx.state.get("runs", 0)
    await ctx.state.set("runs", runs + 1)
    print(f"Run #{runs + 1}")
```

### Lifecycle Hooks

```python
@app.on_startup
async def bootstrap(app):
    print("App starting")


@app.on_shutdown
async def cleanup(app):
    print("App shutting down")
```

### YAML Configuration

```yaml
app:
  name: my-app

resources:
  rmq:
    type: rabbitmq
    url: amqp://guest:guest@localhost/
  timer:
    type: interval
    minutes: 5
  queue:
    type: rabbitmq_queue
    connector: rmq
    queue: jobs
  notify:
    type: http_sink
    url: "https://example.com/hooks/jobs"

tasks:
  - name: process_jobs
    source: timer
    emit: [queue, notify]
    handler:
      ref: myapp.tasks:process_jobs
    retry:
      type: max_attempts
      max_attempts: 3
```

### Worker Packaging

```bash
onestep check --strict worker.yaml
onestep build worker.yaml --strict --out dist/worker.zip
```

`onestep build` collects the YAML entry point, Python handler/hook/conditional route references, dependency declaration files, and metadata such as README/license, producing a deployable zip with a `onestep-package.json` manifest.

## Advanced Features

### Task Orchestration

```python
from onestep import MemoryQueue

input_queue = MemoryQueue("input")
stage1_out = MemoryQueue("stage1")
stage2_out = MemoryQueue("stage2")


@app.task(source=input_queue, emit=stage1_out)
async def stage1(ctx, item):
    return item * 2


@app.task(source=stage1_out, emit=stage2_out)
async def stage2(ctx, item):
    return item + 1


@app.task(source=stage2_out)
async def final(ctx, item):
    print(f"Result: {item}")
```

### Graceful Shutdown

```python
app = OneStepApp("demo", shutdown_timeout_s=30.0)


@app.task(source=...)
async def task(ctx, item):
    await process(item)


@app.task(source=...)
async def shutdown_trigger(ctx, item):
    ctx.app.request_shutdown()
```

### Configuration Management

```python
app = OneStepApp(
    "my-app",
    config={
        "region": "cn",
        "debug": True,
        "batch_size": 100,
    },
)


@app.task(source=...)
async def task(ctx, item):
    region = ctx.config["region"]
    debug = ctx.config.get("debug", False)
```

### Control Plane Integration

First install the control-plane reporter plugin:

```bash
pip install 'onestep[control-plane]'
```

```python
from onestep import ControlPlaneReporter, ControlPlaneReporterConfig

app = OneStepApp("my-app")
reporter = ControlPlaneReporter(
    ControlPlaneReporterConfig.from_env(
        app_name=app.name,
        service_description="Sync billing data to data warehouse",
    )
)
reporter.attach(app)
```

You can also use `reporter: true` and inject the service description via `ONESTEP_SERVICE_DESCRIPTION`.
The reporter pushes topology sync, heartbeats, metrics, and events, and can receive remote task control commands such as `pause_task`, `resume_task`, and `restart_task`. Task handlers can report low-cardinality custom metrics via `ctx.metrics.counter(...).inc()` and `ctx.metrics.gauge(...).set()`.

## Comparison with 0.5.x

| Feature | 0.5.x | 1.x |
|---------|-------|-----|
| Decorator | `@step` | `@app.task` |
| Message source | `from_broker` | `source` |
| Message output | `to_broker` | `emit` |
| Concurrency control | `workers` | `concurrency` |
| Start method | `step.start()` | `app.run()` / CLI |
| Retry strategy | `TimesRetry` etc. | `MaxAttempts` |
| Middleware | `BaseMiddleware` | Event hooks |
| State | None | `ctx.state` |
| Configuration | None | `ctx.config` |
| Lifecycle | Limited | `@app.on_startup/shutdown` |

## Next Steps

- [Tutorial](/guide/tutorial) - quick start
- [CLI Deploy](/guide/deploy) - production deployment
- [Worker Runtime Image](/guide/worker-runtime-image) - containerized YAML workers
- [RabbitMQ](/broker/rabbitmq) - distributed queue
- [Core Reliability](/core-reliability) - runtime and plugin compatibility contracts

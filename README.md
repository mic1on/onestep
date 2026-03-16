# onestep

<div align=center><img src="https://onestep.code05.com/logo-3.svg" width="300"></div>
<div align=center>
<a href="https://pypi.org/project/onestep" target="_blank">
    <img src="https://img.shields.io/pypi/pyversions/onestep.svg" alt="Supported Python versions">
</a>
</div>
<hr />

`onestep` is a small async task runtime centered on four concepts:

- `OneStepApp`: task registry and lifecycle manager
- `Source`: fetch data from a queue or polling backend
- `Sink`: publish processed data
- `Delivery`: a single fetched item with `ack/retry/fail`

The V1 stable surface includes:

- `MemoryQueue`
- `MySQLConnector.table_queue(...)`
- `MySQLConnector.incremental(...)`
- `MySQLConnector.table_sink(...)`
- `MySQLConnector.state_store(...)`
- `MySQLConnector.cursor_store(...)`
- `RabbitMQConnector.queue(...)`
- `RedisConnector.stream(...)`
- `SQSConnector.queue(...)`
- `IntervalSource.every(...)`
- `CronSource(...)`
- `WebhookSource(...)`

## Install

Core package:

```bash
pip install onestep
```

Common extras:

- `pip install 'onestep[yaml]'`
- `pip install 'onestep[mysql]'`
- `pip install 'onestep[rabbitmq]'`
- `pip install 'onestep[redis]'`
- `pip install 'onestep[sqs]'`
- `pip install 'onestep[all]'`

From a source checkout:

- `pip install -e .`
- `pip install -e '.[dev]'`
- `pip install -e '.[integration]'`

## Upgrading from 0.5.x

`1.0.0` is a runtime rewrite. Projects built on the legacy `step` and
`*Broker` APIs should treat the upgrade as a migration, not a drop-in package
bump.

See `MIGRATION-0.5-to-1.0.0.md` for:

- old-to-new API mapping
- unsupported legacy features
- a minimal before/after example
- rollout guidance for existing projects

## CLI

The deployment entrypoint is the `onestep` CLI.

Recommended module shape:

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, _):
    print("syncing billing data")
```

Run the app:

```bash
onestep run your_package.tasks:app
```

The short form is also supported:

```bash
onestep your_package.tasks:app
```

Check the target before starting it:

```bash
onestep check your_package.tasks:app
```

You can also point the CLI at a zero-argument factory:

```bash
onestep check your_package.tasks:build_app
```

Use JSON output when you want the check result in CI or deployment scripts:

```bash
onestep check --json your_package.tasks:app
```

You can also load a YAML app definition with `handler.ref` entries that point to Python callables:

```yaml
app:
  name: billing-sync

connectors:
  tick:
    type: interval
    minutes: 5
    immediate: true
  processed:
    type: memory

tasks:
  - name: sync_billing
    source: tick
    handler:
      ref: your_package.handlers.billing:sync_billing
      params:
        region: cn
    emit: [processed]
    retry:
      type: max_attempts
      max_attempts: 3
      delay_s: 10
```

Check or run the YAML target the same way:

```bash
onestep check worker.yaml
onestep run worker.yaml
```

YAML resources can reference other resources by name, for example `rabbitmq_queue.connector: rmq`
or `mysql_incremental.state: cursor_store`.

Currently supported YAML resource types:

- `memory`
- `interval`
- `cron`
- `webhook`
- `rabbitmq`
- `rabbitmq_queue`
- `redis`
- `redis_stream`
- `sqs`
- `sqs_queue`
- `mysql`
- `mysql_state_store`
- `mysql_cursor_store`
- `mysql_table_queue`
- `mysql_incremental`
- `mysql_table_sink`

YAML apps can also bind app-level state explicitly:

```yaml
app:
  name: billing-sync
  state: app_state

connectors:
  app_state:
    type: mysql_state_store
    connector: db
    table: onestep_state
```

The named state resource must support `load/save/delete`; `mysql_state_store`
and `mysql_cursor_store` both work.

Runnable examples live in:

- `example/cli_app.py`
- `example/cli_app.yaml`

Run it locally with:

```bash
PYTHONPATH=src onestep check example.cli_app:app
onestep check example/cli_app.yaml
SYNC_INTERVAL_SECONDS=5 PYTHONPATH=src onestep run example.cli_app:app
```

### systemd

A complete deployment template lives in:

- `deploy/README.md`
- `deploy/systemd/onestep-app.service`
- `deploy/env/onestep-app.env.example`
- `deploy/bin/onestep-preflight.sh`

The example uses:

- `/etc/onestep/onestep-app.env` for deployment variables
- `ExecStartPre` to run a startup check
- `ExecStart` to launch `onestep run`

Install it with:

```bash
sudo mkdir -p /etc/onestep
sudo cp deploy/env/onestep-app.env.example /etc/onestep/onestep-app.env
sudo cp deploy/systemd/onestep-app.service /etc/systemd/system/onestep-app.service
sudo systemctl daemon-reload
sudo systemctl enable --now onestep-app
```

Check status and logs:

```bash
sudo systemctl status onestep-app
sudo journalctl -u onestep-app -f
```

See `deploy/README.md` for the expected directory layout and the env vars you need to adjust first.
The deploy template prepends `APP_CWD` to `PYTHONPATH` so module targets defined inside the repo can be imported by the `onestep` console script.

## Control Plane Reporter

`onestep` can push runtime telemetry to `onestep-control-plane` without adding a new connector or changing task code.

Attach the reporter explicitly:

```python
from onestep import ControlPlaneReporter, ControlPlaneReporterConfig, IntervalSource, OneStepApp

app = OneStepApp("billing-sync")
reporter = ControlPlaneReporter(ControlPlaneReporterConfig.from_env(app_name=app.name))
reporter.attach(app)


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_users(ctx, _):
    print("syncing users")
```

Required environment variables:

- `ONESTEP_CONTROL_PLANE_URL` or `ONESTEP_CONTROL_URL`
- `ONESTEP_CONTROL_PLANE_TOKEN` or `ONESTEP_CONTROL_TOKEN`

Common optional environment variables:

- `ONESTEP_ENV`
- `ONESTEP_SERVICE_NAME`
- `ONESTEP_NODE_NAME`
- `ONESTEP_DEPLOYMENT_VERSION`
- `ONESTEP_INSTANCE_ID`
- `ONESTEP_CONTROL_PLANE_HEARTBEAT_INTERVAL_S`
- `ONESTEP_CONTROL_PLANE_METRICS_INTERVAL_S`
- `ONESTEP_CONTROL_PLANE_EVENT_FLUSH_INTERVAL_S`
- `ONESTEP_CONTROL_PLANE_EVENT_BATCH_SIZE`
- `ONESTEP_CONTROL_PLANE_TIMEOUT_S`

The reporter currently pushes:

- `POST /api/v1/agents/sync`
- `POST /api/v1/agents/heartbeat`
- `POST /api/v1/agents/metrics`
- `POST /api/v1/agents/events`

Behavior:

- startup sends an initial heartbeat
- startup also sends a topology sync built from the current app tasks
- sync is retried on later heartbeat cycles until the current topology hash is accepted
- task execution events are aggregated into task window metrics
- important runtime events (`retried`, `failed`, `dead_lettered`, `cancelled`) are batched and pushed
- reporter failures are logged but do not stop task execution

Quick local demo:

1. Start `onestep-control-plane`:

   If the control plane repo is checked out next to this one:

```bash
cd ../onestep-control-plane
./scripts/start-local.sh
```

2. Start a long-running OneStep reporter demo:

```bash
./scripts/run-control-plane-demo.sh
```

3. Inspect the control plane:

```text
http://127.0.0.1:8080/api/v1/services?environment=dev
http://127.0.0.1:8080/openapi.json
```

## Runtime

The runtime now has a few explicit control points:

- `OneStepApp(..., shutdown_timeout_s=30.0)` controls how long the app waits for inflight tasks before cancelling them during shutdown
- `@app.task(..., timeout_s=30.0)` applies an execution timeout to async handlers
- `@app.task(..., dead_letter=...)` routes terminal failures into a dead-letter sink
- `@app.on_event` receives task execution events
- `InMemoryMetrics()` is a built-in metrics hook for event counting
- `@app.on_startup` and `@app.on_shutdown` register lifecycle hooks
- `ctx.config` exposes app config
- `ctx.state` exposes per-task namespaced state backed by the app state store

Failures are classified as:

- `error`
- `timeout`
- `cancelled`

Custom retry policies receive a `FailureInfo` object so they can decide differently for timeouts vs business exceptions.

Execution events are emitted for:

- `fetched`
- `started`
- `succeeded`
- `retried`
- `failed`
- `dead_lettered`
- `cancelled`

```python
from onestep import InMemoryMetrics, InMemoryStateStore, MemoryQueue, OneStepApp, StructuredEventLogger

source = MemoryQueue("incoming")
dead = MemoryQueue("dead-letter")
metrics = InMemoryMetrics()
app = OneStepApp(
    "runtime-demo",
    config={"prefix": "demo"},
    state=InMemoryStateStore(),
    shutdown_timeout_s=10.0,
)
app.on_event(metrics)
app.on_event(StructuredEventLogger())


@app.on_startup
async def bootstrap(app):
    await source.publish({"value": 1})


@app.task(source=source, timeout_s=5.0, dead_letter=dead)
async def consume(ctx, item):
    runs = await ctx.state.get("runs", 0)
    await ctx.state.set("runs", runs + 1)
    print(ctx.config["prefix"], item)
    ctx.app.request_shutdown()
```

If a task ends in a terminal failure and `dead_letter` is configured, the dead-letter sink receives:

- `body["payload"]`: the original payload
- `body["failure"]`: `{kind, exception_type, message}`
- `meta["original_meta"]`: the original envelope metadata

You can inspect metrics in-process:

```python
snapshot = metrics.snapshot()
print(snapshot["kinds"])
```

`StructuredEventLogger()` bridges `TaskEvent` into standard Python logging with consistent fields such as:

- `event_kind`
- `app_name`
- `task_name`
- `source_name`
- `attempts`
- `duration_s`
- `failure_kind`

## Memory Queue Example

```python
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("demo")
source = MemoryQueue("incoming")
sink = MemoryQueue("processed")


@app.task(source=source, emit=sink, concurrency=4)
async def double(ctx, item):
    ctx.app.request_shutdown()
    return {"value": item["value"] * 2}


async def main():
    await source.publish({"value": 21})
    await app.serve()

```

## Interval Source

Use a local scheduler when you need to run a task every fixed duration.

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("interval-demo")


@app.task(
    source=IntervalSource.every(
        hours=1,
        immediate=True,
        overlap="skip",
        payload={"job": "refresh-cache"},
    )
)
async def refresh_cache(ctx, item):
    print("scheduled at:", ctx.current.meta["scheduled_at"], "payload:", item)
```

`overlap` controls what happens when the previous run is still inflight:

- `allow`: start another run immediately
- `skip`: drop missed ticks while the previous run is still running
- `queue`: serialize missed ticks and run them one by one afterwards

## Cron Source

Use `CronSource` when you care about wall-clock time rather than elapsed duration.

```python
from onestep import CronSource, OneStepApp

app = OneStepApp("hourly-sync")


@app.task(source=CronSource("0 * * * *", timezone="Asia/Shanghai", overlap="skip"))
async def sync_hourly(ctx, _):
    print("running at:", ctx.current.meta["scheduled_at"])
```

The built-in parser supports standard 5-field cron expressions and these aliases:

- `@hourly`
- `@daily`
- `@weekly`
- `@monthly`
- `@yearly`

## Webhook Source

Use `WebhookSource` when external systems push events into your app.

```python
from onestep import BearerAuth, MemoryQueue, OneStepApp, WebhookSource

app = OneStepApp("webhook-demo")
jobs = MemoryQueue("jobs")


@app.task(
    source=WebhookSource(
        path="/webhooks/github",
        methods=("POST",),
        host="127.0.0.1",
        port=8080,
        auth=BearerAuth("replace-me"),
    ),
    emit=jobs,
)
async def ingest_github(ctx, event):
    return {
        "event": event["headers"].get("x-github-event"),
        "payload": event["body"],
    }
```

The stdlib implementation supports:

- shared `host:port` listeners across multiple webhook routes
- optional `BearerAuth(...)`
- `json`, `form`, `text`, `raw`, and `auto` body parsing
- fixed `WebhookResponse(...)` responses
- exact path matching and method filtering

The payload delivered to your task contains:

- `body`
- `headers`
- `query`
- `method`
- `path`
- `client`
- `received_at`

## MySQL Table Queue

Use a table as a task queue by claiming rows and marking them as finished.

```python
from onestep import MemoryQueue, MySQLConnector, OneStepApp

app = OneStepApp("orders")
db = MySQLConnector("mysql+pymysql://root:root@localhost:3306/app")
source = db.table_queue(
    table="orders",
    key="id",
    where="status = 0",
    claim={"status": 9},
    ack={"status": 1},
    nack={"status": 0},
    batch_size=100,
)
sink = db.table_sink(table="processed_orders", mode="upsert", keys=("id",))


@app.task(source=source, emit=sink, concurrency=16)
async def process_order(ctx, row):
    return {"id": row["id"], "payload": row["payload"], "status": "done"}
```

## MySQL Incremental Sync

Use `(updated_at, id)` as a lightweight cursor for Logstash-style sync.

```python
from onestep import MemoryQueue, MySQLConnector, OneStepApp

app = OneStepApp("sync-users")
db = MySQLConnector("mysql+pymysql://root:root@localhost:3306/app")
state = db.cursor_store(table="onestep_cursor")
source = db.incremental(
    table="users",
    key="id",
    cursor=("updated_at", "id"),
    where="deleted = 0",
    batch_size=1000,
    state=state,
)
out = MemoryQueue("dw")


@app.task(source=source, emit=out, concurrency=1)
async def sync_user(ctx, row):
    return {"id": row["id"], "name": row["name"], "updated_at": row["updated_at"]}
```

For production deployments, prefer `db.cursor_store(...)` or `db.state_store(...)` over the in-memory stores so cursors and task state survive process restarts.


## RabbitMQ Queue

```python
from onestep import OneStepApp, RabbitMQConnector

app = OneStepApp("rabbitmq-demo")
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
source = rmq.queue(
    "incoming_jobs",
    exchange="jobs.events",
    routing_key="jobs.created",
    prefetch=50,
)
out = rmq.queue(
    "processed_jobs",
    exchange="jobs.events",
    routing_key="jobs.done",
)


@app.task(source=source, emit=out, concurrency=8)
async def process_job(ctx, item):
    return {"job": item["job"], "status": "done"}
```

Install with `pip install '.[rabbitmq]'`.

## Redis Streams

Use Redis Streams for lightweight, reliable message queuing with consumer groups.

```python
from onestep import OneStepApp, RedisConnector

app = OneStepApp("redis-demo")
redis = RedisConnector("redis://localhost:6379")
source = redis.stream(
    "jobs",
    group="workers",
    batch_size=100,
    poll_interval_s=0.5,
)
out = redis.stream("processed")


@app.task(source=source, emit=out, concurrency=8)
async def process_job(ctx, item):
    return {"job": item["job"], "status": "done"}
```

Key features:

- **Consumer groups**: Multiple consumers share message processing
- **Message acknowledgment**: `XACK` for reliable processing
- **Pending messages**: Unacked messages stay in PEL for retry via `XCLAIM`
- **Stream trimming**: `maxlen` option to limit stream size

Install with `pip install '.[redis]'`.

## SQS Queue

```python
from onestep import OneStepApp, SQSConnector

app = OneStepApp("sqs-demo")
sqs = SQSConnector(region_name="ap-southeast-1")
source = sqs.queue(
    "https://sqs.ap-southeast-1.amazonaws.com/123456789/jobs.fifo",
    message_group_id="workers",
    delete_batch_size=10,
    delete_flush_interval_s=0.5,
    heartbeat_interval_s=15,
    heartbeat_visibility_timeout=60,
)
out = sqs.queue(
    "https://sqs.ap-southeast-1.amazonaws.com/123456789/processed.fifo",
    message_group_id="workers",
)


@app.task(source=source, emit=out, concurrency=16)
async def process_job(ctx, item):
    return {"job": item["job"], "status": "done"}
```

Install with `pip install '.[sqs]'`.

## Examples

Supported examples are indexed in:

- `example/README.md`

Common entrypoints:

- `example/cli_app.py`
- `example/runtime_showcase.py`
- `example/mysql_incremental.py`
- `example/redis_stream.py`
- `example/webhook_source.py`


## Integration Tests

Optional live tests are under `tests/integration/`.
The local stack now includes RabbitMQ, LocalStack SQS, and MySQL.

Install the live-test dependencies:

```bash
pip install '.[integration]'
```

Start the local integration stack:

```bash
make integration-up
```

Load the generated environment into your current shell:

```bash
eval "$(./scripts/setup-integration-env.sh)"
```

Run all live tests in one command:

```bash
make integration-test
```

You can also run one test file manually after loading the environment:

- `PYTHONPATH=src python3 -m pytest tests/integration/test_rabbitmq_live.py -q`
- `PYTHONPATH=src python3 -m pytest tests/integration/test_sqs_live.py -q`
- `PYTHONPATH=src python3 -m pytest tests/integration/test_mysql_live.py -q`

Set `KEEP_INTEGRATION_SERVICES=1` to keep containers running after `make integration-test`.

## Test Layout

The test suite is now intentionally split by responsibility:

- `tests/contract/`: runtime contract tests that lock task execution semantics
- `tests/integration/`: live infrastructure tests for RabbitMQ, SQS, and MySQL
- `tests/test_*.py`: connector-focused unit tests

## End-to-End Demo

For a quick end-to-end demo with webhook ingestion, queueing, dead-letter handling, metrics, and structured logs:

```bash
PYTHONPATH=src python3 example/runtime_showcase.py
```

Then send:

```bash
curl -X POST http://127.0.0.1:8090/demo/webhook \
  -H 'Content-Type: application/json' \
  -d '{"action":"ok","value":21}'

curl -X POST http://127.0.0.1:8090/demo/webhook \
  -H 'Content-Type: application/json' \
  -d '{"action":"fail","value":21}'

curl -X POST http://127.0.0.1:8090/demo/webhook \
  -H 'Content-Type: application/json' \
  -d '{"action":"slow","value":21}'
```

You will see:

- structured task event logs
- successful processing for `action=ok`
- dead-letter output for `action=fail`
- timeout + dead-letter output for `action=slow`

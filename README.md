# onestep

<div align=center><img src="https://onestep.code05.com/logo-3.svg" width="300"></div>
<div align=center>
<a href="https://pypi.org/project/onestep" target="_blank">
    <img src="https://img.shields.io/pypi/pyversions/onestep.svg" alt="Supported Python versions">
</a>
</div>

[English](README.md) | [简体中文](README.zh-CN.md)

<hr />

**onestep** is a small async task runtime for queue, polling, schedule, and
webhook workloads. You declare a task with a `source` and optional `sink`, and
the runtime takes care of fetching, concurrency, retries, dead-lettering, and
telemetry.

- **One decorator** turns any async function into a managed task
- **Pluggable connectors** for memory, MySQL, RabbitMQ, Redis, SQS, Kafka, Feishu
- **Scheduling** via interval, cron, webhook, or DB-backed queues
- **Production-ready**: retries, dead-letter, timeouts, state stores, metrics,
  and an optional control-plane reporter
- **Two config styles**: plain Python, or declarative YAML
- Python 3.9+

## Quick start

Install:

```bash
pip install onestep
# optional extras:
pip install 'onestep[yaml]'          # YAML task definitions
pip install 'onestep[control-plane]' # push telemetry to onestep-control-plane
pip install 'onestep[kafka]'         # Kafka topic source/sink, Python 3.10+
```

Define an app, then run it with the `onestep` CLI:

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, _):
    print("syncing billing data")
```

```bash
onestep run your_package.tasks:app
onestep check your_package.tasks:app   # validate the target before starting
```

## What it does

| Capability | Where |
| --- | --- |
| **Fetch work** from a queue, schedule, webhook, or DB cursor | `MemoryQueue`, `IntervalSource`, `CronSource`, `WebhookSource`, MySQL `table_queue` / `incremental` / binlog, RabbitMQ `queue`, Redis `stream`, SQS `queue`, Kafka `kafka_topic` |
| **Emit results** to a downstream sink | any source doubles as a sink; MySQL `table_sink`; Kafka `kafka_topic`; HTTP `http_sink`; Feishu Bitable sink |
| **Schedule** recurring work | `IntervalSource.every(...)`, `CronSource(...)` with overlap control (`allow` / `skip` / `queue`) |
| **Ingest external events** | `WebhookSource` with bearer auth, shared listeners, body parsing |
| **Survive failures** | retry policies, `dead_letter` sink, per-task `timeout_s`, failure classification (`error` / `timeout` / `cancelled`) |
| **Track state** | `InMemoryStateStore`, MySQL state/cursor stores; `ctx.state` namespace per task |
| **Observe** | `@app.on_event` hooks, `InMemoryMetrics`, `StructuredEventLogger`, execution events |
| **Operate** | optional control-plane reporter with remote commands: `ping`, `shutdown`, `restart`, `drain`, `pause_task`, `resume_task`, `restart_task`, `sync_now` |

## Core concepts

The whole runtime is built on four ideas:

- **`OneStepApp`** — task registry and lifecycle manager
- **`Source`** — fetches data from a queue, schedule, webhook, or polling backend
- **`Sink`** — publishes processed results downstream
- **`Delivery`** — a single fetched item exposing `ack` / `retry` / `fail`

```python
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("demo")
source = MemoryQueue("incoming")
sink = MemoryQueue("processed")


@app.task(source=source, emit=sink, concurrency=4)
async def double(ctx, item):
    return {"value": item["value"] * 2}


async def main():
    await source.publish({"value": 21})
    await app.serve()
```

## Connectors

Each backend ships as its own package so you only install what you use:

| Package | Provides | Install |
| --- | --- | --- |
| **core** | `MemoryQueue`, `IntervalSource`, `CronSource`, `WebhookSource`, `http_sink`, runtime | `pip install onestep` |
| **Control plane** | reporter telemetry and remote commands | `pip install 'onestep[control-plane]'` |
| **MySQL** | `table_queue`, `incremental`, binlog CDC, `table_sink`, state/cursor stores | `pip install onestep-mysql` |
| **PostgreSQL** | same primitives as MySQL, backed by PostgreSQL | `pip install onestep-postgres` |
| **RabbitMQ** | `queue` with exchange/routing-key binding and prefetch | `pip install onestep-mq` |
| **Redis** | `stream` with consumer groups, `XACK`, `XCLAIM`, `maxlen` | `pip install onestep-redis` |
| **SQS** | `queue` with batched deletes and heartbeat visibility | `pip install onestep-sqs` |
| **Kafka** | `kafka_topic` source/sink with manual offset commits | `pip install onestep-kafka` |
| **Feishu Bitable** | incremental source and upsert sink | `pip install onestep-feishu-bitable` |

Or install everything at once:

```bash
pip install 'onestep[all]'
```

## Configuration styles

### Plain Python

Best for application code. Each connector is a class you instantiate:

```python
from onestep import OneStepApp
from onestep_redis import RedisConnector

app = OneStepApp("redis-demo")
redis = RedisConnector("redis://localhost:6379")
source = redis.stream("jobs", group="workers", batch_size=100)
out = redis.stream("processed")


@app.task(source=source, emit=out, concurrency=8)
async def process_job(ctx, item):
    return {"job": item["job"], "status": "done"}
```

### YAML

Best for deployment wiring. Keep business logic in Python; describe the
runtime — app, resources, hooks, tasks — declaratively.

```yaml
app:
  name: billing-sync

resources:
  tick:
    type: interval
    minutes: 5
    immediate: true

tasks:
  - name: sync_billing
    source: tick
    handler:
      ref: your_package.handlers.billing:sync_billing
```

```bash
onestep run worker.yaml
onestep check --strict worker.yaml   # schema validation, unknown-field detection
onestep init billing-sync            # scaffold a minimal YAML project
onestep build worker.yaml --out dist/worker.zip
```

The full YAML schema, resource types, conditional routing, and state binding
are covered in [`docs/yaml-task-definition.md`](docs/yaml-task-definition.md).

### Build a deployable worker package

`onestep build` packages a YAML worker project into a zip that a worker agent can
download and run. It validates the target first, collects the YAML entrypoint,
local Python modules referenced by handler/hook refs, dependency declaration
files such as `pyproject.toml`, `requirements.txt`, and `uv.lock`, and writes an
`onestep-package.json` manifest into the zip.

```bash
onestep build worker.yaml --strict --out dist/worker.zip
```

For files that cannot be inferred from imports, add build hints to
`pyproject.toml`:

```toml
[tool.onestep.build]
include = ["templates/**"]
exclude = ["templates/private/**"]
```

Use `--env-file .env` to provide local values for the pre-build check. `.env`
files are excluded from packages by default; deploy-time configuration should be
provided through the worker agent or control plane. Use `--json` to emit the
build report for automation.

## Deployment

- **systemd** — minimal unit + preflight check template in
  [`deploy/`](deploy/README.md)
- **Official worker image** — run YAML workers in Docker without packaging:
  ```bash
  docker run --rm \
    -e ONESTEP_TARGET=/workspace/worker.yaml \
    -v "$PWD:/workspace" \
    ghcr.io/mic1on/onestep-worker:1.5.0
  ```
  See [`deploy/worker-runtime-image.md`](deploy/worker-runtime-image.md).
- **Embed in a web app** — recommended shape for FastAPI/Django in
  [`deploy/web-service-integration.md`](deploy/web-service-integration.md).

## Control plane

`onestep` can push runtime telemetry (heartbeat, topology, metrics, events) to
[`onestep-control-plane`](../onestep-control-plane) over a single WebSocket and
accept remote commands — with no connector or task-code changes.

Install the reporter plugin first:

```bash
pip install 'onestep[control-plane]'
```

```yaml
app:
  name: billing-sync

reporter: true
```

Required env: `ONESTEP_CONTROL_PLANE_URL`, `ONESTEP_CONTROL_PLANE_TOKEN`.

Handlers can report low-cardinality custom counters and gauges through the same
reporter. The plane stores them and can expose them from its Prometheus
`/metrics` endpoint:

```python
async def sync_users(ctx, payload):
    success_count = 0
    failed_count = 0
    ...
    ctx.metrics.counter("rows_success").inc(success_count)
    ctx.metrics.counter("rows_failed").inc(failed_count)
    ctx.metrics.gauge("batch_size").set(success_count + failed_count)
```

For identity, multi-replica guidance, env vars, and a local demo, see
[`docs/stable-instance-identity.md`](docs/stable-instance-identity.md).

## Examples

Runnable examples live in [`example/`](example/README.md). Highlights:

```bash
# 5-second interval task
SYNC_INTERVAL_SECONDS=5 PYTHONPATH=src onestep run example.cli_app:app

# end-to-end: webhook -> queue -> worker -> dead-letter, with metrics + logs
PYTHONPATH=src python3 example/runtime_showcase.py
```

## Upgrading

`1.0.0` was a runtime rewrite. If you're coming from `0.5.x`, see
[`MIGRATION-0.5-to-1.0.0.md`](MIGRATION-0.5-to-1.0.0.md) for the old-to-new API
mapping, unsupported features, and rollout guidance.

## More

- [`docs/yaml-task-definition.md`](docs/yaml-task-definition.md) — YAML schema
- [`docs/core-reliability.md`](docs/core-reliability.md) — stable API,
  delivery semantics, plugin compatibility, and release checklist
- [`docs/stable-instance-identity.md`](docs/stable-instance-identity.md) —
  reporter identity resolution
- [`docs/agent-ws-protocol.md`](docs/agent-ws-protocol.md) — agent WS protocol
- [`deploy/`](deploy/README.md) — deployment templates

## License

MIT

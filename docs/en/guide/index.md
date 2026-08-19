---
title: Quick Start | Guide
outline: deep
---

# Quick Start

onestep is a lightweight Python async task runtime. It's organized around `OneStepApp`, `Source`, `Sink`, and task handler functions, suitable for queue consumption, periodic sync, webhook ingestion, and multi-stage data processing.

Current package version is `1.11.0`. The docs site uses repository-locked VitePress `1.6.4`.

## Installation

::: code-group

```bash [pip]
pip install onestep
```

```bash [uv]
uv add onestep
```

```bash [poetry]
poetry add onestep
```

:::

Install YAML support or connector plugins based on your use case:

::: code-group

```bash [YAML]
pip install 'onestep[yaml]'
```

```bash [MySQL]
pip install onestep-mysql
```

```bash [PostgreSQL]
pip install onestep-postgres
```

```bash [RabbitMQ]
pip install onestep-mq
```

```bash [Redis]
pip install onestep-redis
```

```bash [AWS SQS]
pip install onestep-sqs
```

```bash [Kafka]
pip install onestep-kafka
```

```bash [Control Plane]
pip install 'onestep[control-plane]'
```

```bash [Feishu Bitable]
pip install onestep-feishu-bitable
```

```bash [All]
pip install 'onestep[all]'
```

:::

`onestep[all]` installs common queue, database, Kafka, YAML, and control-plane dependencies; Feishu Bitable is still installed separately.

## Your First Task

Create `tasks.py`:

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("demo")


@app.task(source=IntervalSource.every(seconds=10, immediate=True))
async def hello(ctx, _):
    scheduled_at = ctx.current.meta["scheduled_at"]
    print(f"hello from onestep: {scheduled_at}")


if __name__ == "__main__":
    app.run()
```

Run it:

::: code-group

```bash [CLI]
onestep run tasks:app
```

```bash [Python]
python tasks.py
```

:::

For production, use the CLI as it can check the target before starting:

```bash
onestep check tasks:app
onestep check --json tasks:app
onestep run tasks:app
```

`onestep tasks:app` is shorthand for `onestep run tasks:app`.

Since 1.7.2, `onestep run` writes INFO-level application logs and task lifecycle events to stdout by default. Applications only need to use the standard library logger; the logger name doesn't need to start with `onestep`:

```python
import logging

logger = logging.getLogger("billing.kpi_sync")
```

For log level control, task event toggling, and embedded run boundaries, see [Logging & Task Events](/en/guide/logging).

## Processing Queue Messages

`MemoryQueue` implements both `Source` and `Sink`, making it suitable for local development and testing.

```python
import asyncio

from onestep import MemoryQueue, OneStepApp

app = OneStepApp("memory-pipeline")
source = MemoryQueue("incoming")
sink = MemoryQueue("processed")


@app.task(source=source, emit=sink, concurrency=2)
async def double(ctx, item):
    return {"value": item["value"] * 2}


async def main():
    await source.publish({"value": 21})
    await app.serve()


asyncio.run(main())
```

In real deployments, you typically swap the input or output `MemoryQueue` for external connector plugins such as RabbitMQ, Redis Streams, AWS SQS, MySQL, PostgreSQL, Kafka, Feishu Bitable, or send results to an HTTP Sink.

## Using External Connectors

```python
from onestep import OneStepApp
from onestep_mysql import MySQLConnector
from onestep_rabbitmq import RabbitMQConnector

app = OneStepApp("orders")
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
db = MySQLConnector("mysql+pymysql://user:pass@localhost/app")

jobs = rmq.queue("orders")
rows = db.table_sink(table="processed_orders", mode="upsert", keys=("id",))


@app.task(source=jobs, emit=rows, concurrency=8)
async def process_order(ctx, order):
    return {
        "id": order["id"],
        "status": "processed",
    }
```

## YAML Configuration

After installing `onestep[yaml]`, you can write runtime resources and task topology into `worker.yaml`:

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
      ref: your_package.handlers:sync_billing
```

Check and run:

```bash
onestep check --strict worker.yaml
onestep run worker.yaml
```

`resources` is the recommended approach. The older `connectors`, `sources`, and `sinks` keys are still readable, but new documentation uses `resources` consistently.

To hand off to a worker agent or upload via the control plane, you can package a YAML worker project into a zip:

```bash
onestep build worker.yaml --strict --out dist/worker.zip
```

YAML also supports forwarding messages directly to a Sink. The task below has no `handler`; the runtime sends `incoming` payloads as-is to the HTTP endpoint:

```yaml
resources:
  incoming:
    type: memory
  notify:
    type: http_sink
    url: "https://example.com/hooks/billing"

tasks:
  - name: forward_billing_event
    source: incoming
    emit: notify
```

## Next Steps

- [Tutorial](/en/guide/tutorial) walks through core concepts with complete examples.
- [Connector Overview](/en/broker/) helps you choose among Memory, Cron, Webhook, HTTP Sink, RabbitMQ, Redis, SQS, MySQL, PostgreSQL, or Kafka.
- [YAML Task Definition](/en/yaml-task-definition) explains the full configuration fields and strict validation.
- [Production Deploy](/en/guide/deploy) covers CLI, systemd, and persistent state.
- [Worker Runtime Image](/en/guide/worker-runtime-image) describes running YAML workers in containers.
- [Core Reliability](/en/core-reliability) explains at-least-once, ack, retry, and plugin compatibility contracts.

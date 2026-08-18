---
title: Tutorial | Guide
---

# Tutorial

This tutorial helps you quickly get started with the onestep 1.x API.

## Core Concepts

onestep 1.x is built around four core concepts:

- **OneStepApp**: Task registration and lifecycle manager
- **Source**: Fetches data from queues or polling backends
- **Sink**: Publishes processed data
- **Delivery**: A single fetched message item supporting `ack/retry/fail`

## Basic Example: Memory Queue

The simplest example, using an in-memory queue:

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


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## Scheduled Tasks

Use `IntervalSource` for periodic execution:

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, _):
    print("syncing billing data")


if __name__ == "__main__":
    app.run()
```

The `overlap` parameter controls behavior when the previous execution is still running:
- `allow`: start another execution immediately
- `skip`: skip the missed trigger
- `queue`: queue the missed trigger, executing them in order

## Cron Scheduled Tasks

Use `CronSource` for wall-clock scheduling:

```python
from onestep import CronSource, OneStepApp

app = OneStepApp("hourly-sync")


@app.task(source=CronSource("0 * * * *", timezone="Asia/Shanghai", overlap="skip"))
async def sync_hourly(ctx, _):
    print("running at:", ctx.current.meta["scheduled_at"])


if __name__ == "__main__":
    app.run()
```

Supports standard 5-field cron expressions and aliases: `@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly`

## Webhook Ingestion

Use `WebhookSource` to receive external HTTP requests:

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
        auth=BearerAuth("your-secret-token"),
    ),
    emit=jobs,
)
async def ingest_github(ctx, event):
    return {
        "event": event["headers"].get("x-github-event"),
        "payload": event["body"],
    }


if __name__ == "__main__":
    app.run()
```

## Spider Example: Multi-Stage Processing

Demonstrates a list-page to detail-page spider scenario:

```python
import httpx
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("spider-demo")

# Define queues
page_queue = MemoryQueue("pages")
list_queue = MemoryQueue("list")
detail_queue = MemoryQueue("detail")


@app.task(source=page_queue, emit=list_queue, concurrency=2)
async def crawl_list(ctx, page):
    """Fetch list page, extract URL"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://httpbin.org/anything/{page}")
        url = resp.json().get("url")
        return url


@app.task(source=list_queue, emit=detail_queue, concurrency=4)
async def crawl_detail(ctx, url):
    """Fetch detail page"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.json()


async def main():
    # Simulate 10 page tasks
    for i in range(1, 11):
        await page_queue.publish(i)
    
    await app.serve()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## Running with CLI

Using the CLI as the deployment entry point is recommended:

```python
# tasks.py
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True))
async def sync_billing(ctx, _):
    print("syncing billing data")
```

Run:

```bash
# Check configuration
onestep check tasks:app

# Run the application
onestep run tasks:app

# Or shorthand
onestep tasks:app
```

## YAML Configuration

YAML files can be used to define the application:

```yaml
# worker.yaml
app:
  name: billing-sync

resources:
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
      ref: your_package.handlers:sync_billing
      params:
        region: cn
    emit: [processed]
    retry:
      type: max_attempts
      max_attempts: 3
      delay_s: 10
```

Run:

```bash
onestep check worker.yaml
onestep run worker.yaml
```

## Next Steps

- [Features](/guide/features) - learn about all supported features
- [RabbitMQ](/broker/rabbitmq) - distributed message queue
- [MySQL](/broker/mysql) - database table queue and incremental sync
- [CLI Deploy](/guide/deploy) - production deployment guide

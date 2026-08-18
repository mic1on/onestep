---
title: RabbitMQ | Broker
outline: deep
---

# RabbitMQ

RabbitMQ is the most popular distributed message queue, and onestep provides full support.

## Installation

```bash
pip install onestep-mq
```

## Quick Start

### Start RabbitMQ

Quick start with Docker:

```bash
docker run -d --name rabbitmq \
  --restart=always \
  -p 5672:5672 \
  -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=admin \
  -e RABBITMQ_DEFAULT_PASS=admin \
  rabbitmq:3-management
```

Management UI: `http://localhost:15672` (credentials: admin/admin)

### Basic Usage

```python
from onestep import OneStepApp
from onestep_rabbitmq import RabbitMQConnector

app = OneStepApp("rabbitmq-demo")

# Create connection
rmq = RabbitMQConnector("amqp://admin:admin@localhost/")

# Create queue as Source
source = rmq.queue(
    "incoming_jobs",
    exchange="jobs.events",
    routing_key="jobs.created",
    prefetch=50,
)

# Create queue as Sink
sink = rmq.queue(
    "processed_jobs",
    exchange="jobs.events",
    routing_key="jobs.done",
)


@app.task(source=source, emit=sink, concurrency=8)
async def process_job(ctx, item):
    print(f"Processing job: {item}")
    return {"job": item["job"], "status": "done"}


if __name__ == "__main__":
    app.run()
```

## Queue Configuration

### Basic Parameters

```python
source = rmq.queue(
    queue="my_queue",           # Queue name
    exchange="my_exchange",     # Exchange name
    routing_key="my.key",       # Routing key
    prefetch=50,                # Prefetch count (concurrency control)
)
```

### Exchange Types

```python
# Direct Exchange (default)
rmq.queue("queue", exchange="direct_exchange", routing_key="exact.match")

# Topic Exchange
rmq.queue("queue", exchange="topic_exchange", routing_key="jobs.*")

# Fanout Exchange
rmq.queue("queue", exchange="fanout_exchange")  # No routing_key needed
```

### Queue Declaration Options

```python
source = rmq.queue(
    "my_queue",
    arguments={
        "x-message-ttl": 60000,      # Message TTL (milliseconds)
        "x-max-length": 10000,        # Max queue length
        "x-dead-letter-exchange": "dlx",  # Dead letter exchange
    }
)
```

## Publishing Messages

### Publish via Sink

Task return values are automatically published:

```python
@app.task(source=..., emit=sink)
async def process(ctx, item):
    return {"result": "data"}  # Auto-published to sink
```

### Manual Publish

```python
import asyncio

async def main():
    sink = rmq.queue("my_queue")
    
    # Publish single
    await sink.publish({"job": "data"})
    
    # Publish multiple
    for i in range(100):
        await sink.publish({"id": i})

asyncio.run(main())
```

## Acknowledgment Mechanism

RabbitMQ messages are automatically acknowledged (ack) after successful task completion:

- **Success**: auto ack
- **Retry**: no ack, message re-queued
- **Fail**: nack (optionally re-queue)

```python
@app.task(
    source=source,
    retry=MaxAttempts(max_attempts=3),
)
async def process(ctx, item):
    # After 3 failures, the message is nacked
    raise Exception("Processing failed")
```

## Multiple Consumers

Multiple consumers can be started on the same queue for load balancing:

```python
# Run the same code on multiple machines
# RabbitMQ automatically distributes messages
@app.task(source=source, concurrency=4)
async def process(ctx, item):
    ...
```

## YAML Configuration

```yaml
resources:
  rmq:
    type: rabbitmq
    url: "amqp://admin:admin@localhost/"
  
  jobs:
    type: rabbitmq_queue
    connector: rmq
    queue: "jobs"
    prefetch: 50
  
  results:
    type: rabbitmq_queue
    connector: rmq
    queue: "results"

tasks:
  - name: process_jobs
    source: jobs
    emit: results
    concurrency: 8
```

## Best Practices

### 1. Prefetch Count

Adjust based on task processing time and memory:

```python
# I/O bound tasks: larger prefetch
source = rmq.queue("io_tasks", prefetch=100)

# CPU bound tasks: smaller prefetch
source = rmq.queue("cpu_tasks", prefetch=4)
```

### 2. Dead Letter Queue

Configure dead letter queue for failed messages:

```python
# Dead letter queue
dead_letter = rmq.queue("dead_letter")

# Main queue with dead letter config
source = rmq.queue(
    "main_queue",
    arguments={
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": "dead_letter",
    }
)

@app.task(source=source, dead_letter=dead_letter)
async def process(ctx, item):
    ...
```

### 3. Message Persistence

```python
source = rmq.queue(
    "important_queue",
    durable=True,  # Queue persistence
)
```

Messages are persistent by default; can be disabled per queue:

```python
sink = rmq.queue("transient_queue", persistent=False)
```

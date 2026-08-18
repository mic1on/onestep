---
title: Connector | Core
outline: deep
---

# Connector

Connector is the bridge connecting external systems, used to create Sources and Sinks.

## Overview

```
Source (Input) → Task (Process) → Sink (Output)
```

- **Source**: Fetches messages from external systems (queues, timers, webhooks, etc.)
- **Sink**: Sends processing results to external systems

## Creating a Source

Each Connector provides different Source creation methods:

```python
from onestep import MemoryQueue, OneStepApp
from onestep_rabbitmq import RabbitMQConnector

app = OneStepApp("demo")

# In-memory queue
memory_source = MemoryQueue("incoming")

# RabbitMQ
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
rabbit_source = rmq.queue("jobs", prefetch=50)

# Timer
from onestep import IntervalSource
timer_source = IntervalSource.every(minutes=5)

# Cron
from onestep import CronSource
cron_source = CronSource("0 * * * *")


@app.task(source=rabbit_source)
async def process(ctx, item):
    ...
```

## Creating a Sink

```python
from onestep import HttpSink, MemoryQueue
from onestep_rabbitmq import RabbitMQConnector

# In-memory queue
memory_sink = MemoryQueue("output")

# RabbitMQ
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
rabbit_sink = rmq.queue("results")

# HTTP
http_sink = HttpSink("notify", url="https://example.com/hooks/results")


@app.task(source=..., emit=rabbit_sink)
async def process(ctx, item):
    return {"processed": item}
```

## Built-in vs Plugin Connectors

| Connector | Purpose | Source | Sink |
|-----------|---------|--------|------|
| `MemoryQueue` | In-memory queue (test/dev) | Yes | Yes |
| `IntervalSource` | Fixed-interval timer | Yes | No |
| `CronSource` | Cron timer | Yes | No |
| `WebhookSource` | HTTP receiver | Yes | No |
| `HttpSink` | HTTP JSON output | No | Yes |
| `RabbitMQConnector` (`onestep-mq`) | RabbitMQ | Yes | Yes |
| `RedisConnector` (`onestep-redis`) | Redis Streams | Yes | Yes |
| `SQSConnector` (`onestep-sqs`) | AWS SQS | Yes | Yes |
| `MySQLConnector` (`onestep-mysql`) | MySQL table queue/incremental sync/binlog CDC/table sink | Yes | Yes |
| `PostgresConnector` (`onestep-postgres`) | PostgreSQL table queue/incremental sync/table sink/tracked execution | Yes | Yes |
| `MongoDBConnector` (`onestep-mongodb`) | MongoDB collection polling/Change Stream/table sink | Yes | Yes |
| `ElasticsearchConnector` (`onestep-elasticsearch`) | Elasticsearch / OpenSearch async bulk sink | No | Yes |
| `ClickHouseConnector` (`onestep-clickhouse`) | ClickHouse async acknowledged table sink | No | Yes |
| `KafkaConnector` (`onestep-kafka`) | Kafka topic consume and produce | Yes | Yes |
| `FeishuBitableConnector` (`onestep-feishu-bitable`) | Feishu Bitable incremental sync/table sink | Yes | Yes |

The `onestep` core package includes in-memory, timer, webhook, and HTTP Sink connectors. RabbitMQ, Redis Streams, AWS SQS, MySQL, PostgreSQL, MongoDB, Elasticsearch/OpenSearch, ClickHouse, Kafka, and Feishu Bitable require installing the corresponding plugin package and importing from the plugin module.

## Mixing Connectors

Different Connectors can be freely combined:

```python
from onestep import (
    CronSource, MemoryQueue, OneStepApp
)
from onestep_mysql import MySQLConnector
from onestep_rabbitmq import RabbitMQConnector

app = OneStepApp("mixed-demo")

# Scheduled trigger
timer = CronSource("0 */6 * * *")  # Every 6 hours

# Send results to RabbitMQ
rmq = RabbitMQConnector("amqp://...")
sink = rmq.queue("processed")


@app.task(source=timer, emit=sink)
async def scheduled_task(ctx, _):
    # Scheduled execution, results sent to MQ
    return {"status": "done", "timestamp": time.time()}


# Another task consumes from RabbitMQ, writes results to MySQL
db = MySQLConnector("mysql+pymysql://...")
db_sink = db.table_sink(table="results", mode="upsert", keys=("id",))


@app.task(source=sink, emit=db_sink)  # Note: previous task's sink is this task's source
async def save_to_db(ctx, item):
    return item
```

## YAML Configuration

Defining Connectors in YAML:

```yaml
resources:
  tick:
    type: interval
    minutes: 5
    immediate: true
  
  rmq:
    type: rabbitmq
    url: "amqp://guest:guest@localhost/"
  
  jobs_queue:
    type: rabbitmq_queue
    connector: rmq
    queue: "jobs"
    prefetch: 50

  results_queue:
    type: rabbitmq_queue
    connector: rmq
    queue: "results"

  notify:
    type: http_sink
    url: "https://example.com/hooks/results"

tasks:
  - name: process_jobs
    source: jobs_queue
    emit: [results_queue, notify]
    handler:
      ref: myapp.tasks:process_jobs
```

## Custom Connector

Implement the `Source` or `Sink` interface:

```python
from onestep import Delivery, Envelope, Sink, Source
import aiohttp

class HTTPSource(Source):
    """Fetch data from an HTTP endpoint"""
    
    def __init__(self, url: str, interval: float = 60.0):
        super().__init__("http-source")
        self.url = url
        self.interval = interval
    
    async def fetch(self, limit: int) -> list[Delivery]:
        async with aiohttp.ClientSession() as session:
            async with session.get(self.url) as resp:
                data = await resp.json()
                return [MyDelivery(item) for item in data[:limit]]
    
class MyDelivery(Delivery):
    def __init__(self, body):
        super().__init__(Envelope(body=body))

    async def ack(self):
        return None

    async def retry(self, *, delay_s: float | None = None):
        return None

    async def fail(self, exc: Exception | None = None):
        return None


class MySink(Sink):
    """Custom output"""
    
    def __init__(self):
        super().__init__("my-sink")

    async def send(self, envelope: Envelope):
        # Send logic
        ...
```

## Next Steps

- [RabbitMQ](/broker/rabbitmq) - Distributed message queue
- [MySQL](/broker/mysql) - Database table queue
- [Webhook](/broker/webhook) - HTTP receiver
- [HTTP Sink](/broker/http) - HTTP output

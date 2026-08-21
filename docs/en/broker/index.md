---
title: Connectors | Broker
outline: deep
---

# Connectors

onestep 1.x uses `Source` for input and `Sink` for output. Many connectors implement both, so they can be consumed by tasks and also receive upstream task return values.

## Built-in Connectors

### In-Memory

| Connector | Source | Sink | Description |
|-----------|--------|------|-------------|
| [Memory](/en/broker/memory) | Yes | Yes | In-memory queue, suitable for development and testing |

### Timers

| Connector | Source | Sink | Description |
|-----------|--------|------|-------------|
| [Interval](/en/broker/cron) | Yes | No | Fixed interval trigger |
| [Cron](/en/broker/cron) | Yes | No | Cron expression trigger |

### Message Queues

| Connector | Source | Sink | Description |
|-----------|--------|------|-------------|
| [Redis Streams](/en/broker/redis) | Yes | Yes | Redis Streams message queue, install `onestep-redis` |
| [RabbitMQ](/en/broker/rabbitmq) | Yes | Yes | RabbitMQ queue, install `onestep-mq` |
| [AWS SQS](/en/broker/sqs) | Yes | Yes | AWS SQS managed queue, install `onestep-sqs` |
| [Kafka](/en/broker/kafka) | Yes | Yes | Kafka topic source/sink, install `onestep-kafka` |

### Databases

| Connector | Source | Sink | Description |
|-----------|--------|------|-------------|
| [MySQL](/en/broker/mysql) | Yes | Yes | Table queue/incremental sync/binlog CDC/table sink, install `onestep-sql[mysql]` |
| [PostgreSQL](/en/broker/postgres) | Yes | Yes | Table queue/incremental sync/table sink/tracked execution, install `onestep-sql[postgres]` |
| [MongoDB](/en/broker/mongodb) | Yes | Yes | Collection polling/Change Stream/table sink, install `onestep-mongodb` |
| [Elasticsearch / OpenSearch](/en/broker/elasticsearch) | No | Yes | Async bulk Sink, install `onestep-elasticsearch` |
| [ClickHouse](/en/broker/clickhouse) | No | Yes | Async confirmed table output Sink, install `onestep-clickhouse` |
| [Feishu Bitable](/en/broker/feishu-bitable) | Yes | Yes | Feishu Bitable incremental sync/table sink, install `onestep-feishu-bitable` |

### Web

| Connector | Source | Sink | Description |
|-----------|--------|------|-------------|
| [Webhook](/en/broker/webhook) | Yes | No | HTTP request reception |
| [HTTP Sink](/en/broker/http) | No | Yes | HTTP JSON request output |

### Custom

| Connector | Source | Sink | Description |
|-----------|--------|------|-------------|
| [Custom](/en/broker/custom) | Yes | Yes | Implement any data source |

## Selection Guide

### Development & Testing

```python
from onestep import MemoryQueue

source = MemoryQueue("test")
```

### Production - Distributed Tasks

```python
from onestep_rabbitmq import RabbitMQConnector

rmq = RabbitMQConnector("amqp://...")
source = rmq.queue("jobs")
```

### Production - Cloud Native

```python
from onestep_sqs import SQSConnector

sqs = SQSConnector(region_name="us-east-1")
source = sqs.queue("https://sqs...")
```

### Database Driven

```python
from onestep_mysql import MySQLConnector

db = MySQLConnector("mysql+pymysql://...")
source = db.table_queue(
    table="tasks",
    key="id",
    where="status = 0",
    claim={"status": 1},
    ack={"status": 2},
    nack={"status": 0},
)
```

### Scheduled Tasks

```python
from onestep import CronSource, IntervalSource

# Fixed interval
source = IntervalSource.every(minutes=5)

# Specific time
source = CronSource("0 9 * * *")
```

### External Integration

```python
from onestep import HttpSink, WebhookSource

# Receive external system push
source = WebhookSource(path="/webhooks/github")

# Send processing results to external HTTP endpoint
sink = HttpSink("notify", url="https://example.com/hooks/events")
```

## YAML Configuration

```yaml
resources:
  memory:
    type: memory
  
  timer:
    type: interval
    minutes: 5
  
  cron:
    type: cron
    expression: "0 9 * * *"
  
  rmq:
    type: rabbitmq
    url: "amqp://..."
  
  jobs:
    type: rabbitmq_queue
    connector: rmq
    queue: "jobs"
  
  db:
    type: mysql
    dsn: "mysql+pymysql://..."
  
  tasks:
    type: mysql_table_queue
    connector: db
    table: "tasks"
  
  webhook:
    type: webhook
    path: "/webhook"
    port: 8080

  notify:
    type: http_sink
    url: "https://example.com/hooks/events"

tasks:
  - name: process_jobs
    source: jobs
    emit: notify
    handler:
      ref: myapp:process_jobs
```

YAML registers resource types through installed plugins. Before using `rabbitmq`, `redis_stream`, `sqs_queue`, `mysql_table_queue`, `postgres_incremental`, `mongodb_polling`, `elasticsearch_bulk_sink`, `clickhouse_table_sink`, `kafka_topic` or `feishu_bitable_*`, install the corresponding plugin in the worker environment.

## Custom Source/Sink

Refer to [Custom Broker](/en/broker/custom) to implement custom data sources.

## Next Steps

- [Memory](/en/broker/memory) - In-memory queue
- [RabbitMQ](/en/broker/rabbitmq) - RabbitMQ queue
- [Kafka](/en/broker/kafka) - Kafka topic source/sink
- [Feishu Bitable](/en/broker/feishu-bitable) - Feishu Bitable sync
- [MySQL](/en/broker/mysql) - MySQL integration
- [PostgreSQL](/en/broker/postgres) - PostgreSQL integration
- [Webhook](/en/broker/webhook) - HTTP reception
- [HTTP Sink](/en/broker/http) - HTTP output
- [Custom](/en/broker/custom) - Custom implementation

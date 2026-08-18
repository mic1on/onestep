---
title: Kafka | Broker
outline: deep
---

# Kafka

`onestep-kafka` provides Kafka topic source/sink using `aiokafka`, requiring Python 3.10+.

## Installation

```bash
pip install onestep-kafka
```

Or install via core extra:

```bash
pip install 'onestep[kafka]'
```

## Basic Usage

```python
from onestep import OneStepApp
from onestep_kafka import KafkaConnector

app = OneStepApp("orders")
kafka = KafkaConnector("localhost:9092")

orders = kafka.topic(
    "orders.events",
    group_id="onestep-orders",
    batch_size=100,
)
processed = kafka.topic("orders.processed")


@app.task(source=orders, emit=processed, concurrency=8)
async def process_order(ctx, order):
    return {
        "id": order["id"],
        "status": "processed",
    }
```

## Delivery Semantics

The Kafka plugin disables Kafka auto commit. Offsets are only committed after onestep processes `ack()` or terminal `fail()`.

This maintains onestep's at-least-once contract: if the task has already written results to a downstream sink but the process exits before offset commit, input messages may be replayed and downstream output may also be duplicated. To avoid duplicates, handlers and sinks should be designed idempotently.

## YAML Configuration

After installing the plugin, YAML can use `kafka` and `kafka_topic`:

```yaml
resources:
  kafka_main:
    type: kafka
    bootstrap_servers: "${KAFKA_BOOTSTRAP_SERVERS}"

  orders:
    type: kafka_topic
    connector: kafka_main
    topic: orders.events
    group_id: onestep-orders
    batch_size: 100
    poll_timeout_ms: 1000

  processed:
    type: kafka_topic
    connector: kafka_main
    topic: orders.processed

tasks:
  - name: process_orders
    source: orders
    emit: processed
    handler:
      ref: worker.tasks:process_order
```

## Common Parameters

| Field | Description |
|-------|-------------|
| `bootstrap_servers` | Kafka bootstrap server string or list |
| `topic` | Topic name |
| `group_id` | Required when used as a source consumer |
| `batch_size` | Max messages to pull per poll |
| `poll_timeout_ms` | Poll timeout |
| `consumer_options` | Options passed through to `AIOKafkaConsumer` |
| `producer_options` | Options passed through to `AIOKafkaProducer` |

## Next Steps

- [YAML Task Definition](/en/yaml-task-definition) - View `emit`, retry, and dead-letter
- [Core Reliability](/en/core-reliability) - Understand at-least-once, ack, and sink send ordering

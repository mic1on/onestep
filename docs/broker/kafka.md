---
title: Kafka | Broker
outline: deep
---

# Kafka

`onestep-kafka` 提供 Kafka topic source/sink，使用 `aiokafka`，要求 Python 3.10+。

## 安装

```bash
pip install onestep-kafka
```

也可以通过 core extra 安装：

```bash
pip install 'onestep[kafka]'
```

## 基本用法

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

## 交付语义

Kafka 插件关闭 Kafka auto commit。offset 只会在 onestep 处理到 `ack()` 或 terminal `fail()` 后提交。

这保持了 onestep 的 at-least-once 契约：如果任务已经把结果写入下游 sink，但进程在 offset commit 前退出，输入消息可能重放，下游输出也可能重复。需要避免重复时，handler 和 sink 应设计为幂等。

## YAML 配置

安装插件后，YAML 可以使用 `kafka` 和 `kafka_topic`：

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

## 常用参数

| 字段 | 说明 |
|---|---|
| `bootstrap_servers` | Kafka bootstrap server 字符串或列表 |
| `topic` | topic 名称 |
| `group_id` | 作为 source 消费时必需 |
| `batch_size` | 每次最多拉取的消息数 |
| `poll_timeout_ms` | 拉取超时时间 |
| `consumer_options` | 透传给 `AIOKafkaConsumer` 的选项 |
| `producer_options` | 透传给 `AIOKafkaProducer` 的选项 |

## 下一步

- [YAML 任务定义](/yaml-task-definition) - 查看 `emit`、retry 和 dead-letter
- [核心可靠性](/core-reliability) - 理解 at-least-once、ack 和 sink 发送顺序

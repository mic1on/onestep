---
title: Connector | 核心
outline: deep
---

# Connector

Connector 是连接外部系统的桥梁，用于创建 Source 和 Sink。

## 概述

```
Source (输入) → Task (处理) → Sink (输出)
```

- **Source**: 从外部系统获取消息（队列、定时器、Webhook 等）
- **Sink**: 将处理结果发送到外部系统

## 创建 Source

每种 Connector 提供不同的 Source 创建方式：

```python
from onestep import MemoryQueue, OneStepApp, RabbitMQConnector

app = OneStepApp("demo")

# 内存队列
memory_source = MemoryQueue("incoming")

# RabbitMQ
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
rabbit_source = rmq.queue("jobs", prefetch=50)

# 定时器
from onestep import IntervalSource
timer_source = IntervalSource.every(minutes=5)

# Cron
from onestep import CronSource
cron_source = CronSource("0 * * * *")


@app.task(source=rabbit_source)
async def process(ctx, item):
    ...
```

## 创建 Sink

```python
from onestep import MemoryQueue, RabbitMQConnector

# 内存队列
memory_sink = MemoryQueue("output")

# RabbitMQ
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
rabbit_sink = rmq.queue("results")


@app.task(source=..., emit=rabbit_sink)
async def process(ctx, item):
    return {"processed": item}
```

## 内置 Connector

| Connector | 用途 | Source | Sink |
|-----------|------|--------|------|
| `MemoryQueue` | 内存队列（测试/开发） | ✅ | ✅ |
| `IntervalSource` | 固定间隔定时器 | ✅ | ❌ |
| `CronSource` | Cron 定时器 | ✅ | ❌ |
| `WebhookSource` | HTTP 接收 | ✅ | ❌ |
| `RabbitMQConnector` | RabbitMQ | ✅ | ✅ |
| `SQSConnector` | AWS SQS | ✅ | ✅ |
| `MySQLConnector` | MySQL 表队列 | ✅ | ✅ |

## 混合使用

不同 Connector 可以自由组合：

```python
from onestep import (
    CronSource, MemoryQueue, MySQLConnector, 
    OneStepApp, RabbitMQConnector
)

app = OneStepApp("mixed-demo")

# 定时触发
timer = CronSource("0 */6 * * *")  # 每 6 小时

# 处理结果发到 RabbitMQ
rmq = RabbitMQConnector("amqp://...")
sink = rmq.queue("processed")


@app.task(source=timer, emit=sink)
async def scheduled_task(ctx, _):
    # 定时执行，结果发到 MQ
    return {"status": "done", "timestamp": time.time()}


# 另一个任务从 RabbitMQ 消费，结果写 MySQL
db = MySQLConnector("mysql+pymysql://...")
db_sink = db.table_sink(table="results", mode="upsert", keys=("id",))


@app.task(source=sink, emit=db_sink)  # 注意：上一个任务的 sink 作为这个任务的 source
async def save_to_db(ctx, item):
    return item
```

## YAML 配置

在 YAML 中定义 Connector：

```yaml
connectors:
  # 定时器
  tick:
    type: interval
    minutes: 5
    immediate: true
  
  # RabbitMQ
  rmq:
    type: rabbitmq
    url: "amqp://guest:guest@localhost/"
  
  jobs_queue:
    type: rabbitmq_queue
    connector: rmq
    queue: "jobs"
    prefetch: 50

tasks:
  - name: process_jobs
    source: jobs_queue
    emit: results_queue
```

## 自定义 Connector

实现 `Source` 或 `Sink` 接口：

```python
from onestep import Source, Sink, Delivery
import aiohttp

class HTTPSource(Source):
    """从 HTTP 端点拉取数据"""
    
    def __init__(self, url: str, interval: float = 60.0):
        self.url = url
        self.interval = interval
    
    async def fetch(self) -> list[Delivery]:
        async with aiohttp.ClientSession() as session:
            async with session.get(self.url) as resp:
                data = await resp.json()
                return [Delivery(body=item) for item in data]
    
    async def ack(self, delivery: Delivery):
        pass  # HTTP 不需要确认


class MySink(Sink):
    """自定义输出"""
    
    async def publish(self, body, meta=None):
        # 发送逻辑
        ...
```

## 下一步

- [RabbitMQ](/broker/rabbitmq) - 分布式消息队列
- [MySQL](/broker/mysql) - 数据库表队列
- [Webhook](/broker/webhook) - HTTP 接收
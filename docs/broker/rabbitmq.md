---
title: RabbitMQ | Broker
outline: deep
---

# RabbitMQ

RabbitMQ 是最常用的分布式消息队列，onestep 提供完整的支持。

## 安装

```bash
pip install 'onestep[rabbitmq]'
```

## 快速开始

### 启动 RabbitMQ

使用 Docker 快速启动：

```bash
docker run -d --name rabbitmq \
  --restart=always \
  -p 5672:5672 \
  -p 15672:15672 \
  -e RABBITMQ_DEFAULT_USER=admin \
  -e RABBITMQ_DEFAULT_PASS=admin \
  rabbitmq:3-management
```

管理界面：`http://localhost:15672`（用户名/密码：admin/admin）

### 基本使用

```python
from onestep import OneStepApp, RabbitMQConnector

app = OneStepApp("rabbitmq-demo")

# 创建连接
rmq = RabbitMQConnector("amqp://admin:admin@localhost/")

# 创建队列作为 Source
source = rmq.queue(
    "incoming_jobs",
    exchange="jobs.events",
    routing_key="jobs.created",
    prefetch=50,
)

# 创建队列作为 Sink
sink = rmq.queue(
    "processed_jobs",
    exchange="jobs.events",
    routing_key="jobs.done",
)


@app.task(source=source, emit=sink, concurrency=8)
async def process_job(ctx, item):
    print(f"处理任务: {item}")
    return {"job": item["job"], "status": "done"}


if __name__ == "__main__":
    app.run()
```

## 队列配置

### 基本参数

```python
source = rmq.queue(
    queue="my_queue",           # 队列名称
    exchange="my_exchange",     # 交换机名称
    routing_key="my.key",       # 路由键
    prefetch=50,                # 预取数量（并发控制）
)
```

### Exchange 类型

```python
# Direct Exchange（默认）
rmq.queue("queue", exchange="direct_exchange", routing_key="exact.match")

# Topic Exchange
rmq.queue("queue", exchange="topic_exchange", routing_key="jobs.*")

# Fanout Exchange
rmq.queue("queue", exchange="fanout_exchange")  # 无需 routing_key
```

### 队列声明选项

```python
source = rmq.queue(
    "my_queue",
    queue_args={
        "x-message-ttl": 60000,      # 消息 TTL (毫秒)
        "x-max-length": 10000,        # 队列最大长度
        "x-dead-letter-exchange": "dlx",  # 死信交换机
    }
)
```

## 发布消息

### 通过 Sink 发布

任务返回值自动发布：

```python
@app.task(source=..., emit=sink)
async def process(ctx, item):
    return {"result": "data"}  # 自动发布到 sink
```

### 手动发布

```python
import asyncio

async def main():
    sink = rmq.queue("my_queue")
    
    # 发布单条
    await sink.publish({"job": "data"})
    
    # 发布多条
    for i in range(100):
        await sink.publish({"id": i})

asyncio.run(main())
```

## 确认机制

RabbitMQ 消息在任务成功完成后自动确认（ack）：

- **成功**: 自动 ack
- **重试**: 不 ack，消息重新入队
- **失败**: nack（可选择是否重新入队）

```python
@app.task(
    source=source,
    retry=MaxAttempts(max_attempts=3),
)
async def process(ctx, item):
    # 失败 3 次后，消息会被 nack
    raise Exception("处理失败")
```

## 多消费者

同一队列可以启动多个消费者，实现负载均衡：

```python
# 在多台机器上运行相同代码
# RabbitMQ 会自动分配消息
@app.task(source=source, concurrency=4)
async def process(ctx, item):
    ...
```

## YAML 配置

```yaml
connectors:
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

## 最佳实践

### 1. 预取数量

根据任务处理时间和内存调整：

```python
# I/O 密集型任务：较大 prefetch
source = rmq.queue("io_tasks", prefetch=100)

# CPU 密集型任务：较小 prefetch
source = rmq.queue("cpu_tasks", prefetch=4)
```

### 2. 死信队列

配置死信队列处理失败消息：

```python
# 死信队列
dead_letter = rmq.queue("dead_letter")

# 主队列配置死信
source = rmq.queue(
    "main_queue",
    queue_args={
        "x-dead-letter-exchange": "",
        "x-dead-letter-routing-key": "dead_letter",
    }
)

@app.task(source=source, dead_letter=dead_letter)
async def process(ctx, item):
    ...
```

### 3. 消息持久化

```python
source = rmq.queue(
    "important_queue",
    durable=True,  # 队列持久化
)
```

发布时也可以指定：

```python
await sink.publish({"data": "..."}, meta={"delivery_mode": 2})  # 持久化消息
```
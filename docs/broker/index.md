---
title: Broker | Broker
outline: deep
---

# Broker

Broker 是数据源的抽象，包括 Source（输入）和 Sink（输出）。

## 内置 Broker

### 内存

| Broker | Source | Sink | 描述 |
|--------|--------|------|------|
| [Memory](/broker/memory) | ✅ | ✅ | 内存队列，适合开发测试 |

### 定时器

| Broker | Source | Sink | 描述 |
|--------|--------|------|------|
| [Interval](/broker/cron) | ✅ | ❌ | 固定间隔触发 |
| [Cron](/broker/cron) | ✅ | ❌ | Cron 表达式触发 |

### 消息队列

| Broker | Source | Sink | 描述 |
|--------|--------|------|------|
| [Redis Streams](/broker/redis) | ✅ | ✅ | Redis Streams 消息队列 |
| [RabbitMQ](/broker/rabbitmq) | ✅ | ✅ | RabbitMQ 队列 |
| [AWS SQS](/broker/sqs) | ✅ | ✅ | AWS SQS 托管队列 |

### 数据库

| Broker | Source | Sink | 描述 |
|--------|--------|------|------|
| [MySQL](/broker/mysql) | ✅ | ✅ | 表队列/增量同步/表输出 |

### Web

| Broker | Source | Sink | 描述 |
|--------|--------|------|------|
| [Webhook](/broker/webhook) | ✅ | ❌ | HTTP 请求接收 |

### 自定义

| Broker | Source | Sink | 描述 |
|--------|--------|------|------|
| [Custom](/broker/custom) | ✅ | ✅ | 实现任意数据源 |

## 选择指南

### 开发测试

```python
from onestep import MemoryQueue

# 无需外部依赖，快速测试
source = MemoryQueue("test")
```

### 生产环境 - 分布式任务

```python
from onestep import RabbitMQConnector

# 可靠的分布式队列
rmq = RabbitMQConnector("amqp://...")
source = rmq.queue("jobs")
```

### 生产环境 - 云原生

```python
from onestep import SQSConnector

# AWS 托管队列
sqs = SQSConnector(region_name="us-east-1")
source = sqs.queue("https://sqs...")
```

### 数据库驱动

```python
from onestep import MySQLConnector

# 使用数据库表作为队列
db = MySQLConnector("mysql://...")
source = db.table_queue("tasks")
```

### 定时任务

```python
from onestep import CronSource, IntervalSource

# 固定间隔
source = IntervalSource.every(minutes=5)

# 特定时间点
source = CronSource("0 9 * * *")
```

### 外部集成

```python
from onestep import WebhookSource

# 接收外部系统推送
source = WebhookSource(path="/webhooks/github")
```

## YAML 配置

```yaml
connectors:
  # 内存
  memory:
    type: memory
  
  # 定时器
  timer:
    type: interval
    minutes: 5
  
  # Cron
  cron:
    type: cron
    expression: "0 9 * * *"
  
  # RabbitMQ
  rmq:
    type: rabbitmq
    url: "amqp://..."
  
  jobs:
    type: rabbitmq_queue
    connector: rmq
    queue: "jobs"
  
  # MySQL
  db:
    type: mysql
    url: "mysql://..."
  
  tasks:
    type: mysql_table_queue
    connector: db
    table: "tasks"
  
  # Webhook
  webhook:
    type: webhook
    path: "/webhook"
    port: 8080

tasks:
  - name: process_jobs
    source: jobs
    handler:
      ref: myapp:process_jobs
```

## 自定义 Broker

参考 [Custom Broker](/broker/custom) 实现自定义数据源。

## 下一步

- [Memory](/broker/memory) - 内存队列
- [RabbitMQ](/broker/rabbitmq) - RabbitMQ 队列
- [MySQL](/broker/mysql) - MySQL 集成
- [Webhook](/broker/webhook) - HTTP 接收
- [Custom](/broker/custom) - 自定义实现
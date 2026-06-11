---
title: 连接器 | Broker
outline: deep
---

# 连接器

onestep 1.x 使用 `Source` 表示输入，使用 `Sink` 表示输出。很多连接器同时实现两者，因此既能被任务消费，也能接收上游任务返回值。

## 内置连接器

### 内存

| 连接器 | Source | Sink | 描述 |
|--------|--------|------|------|
| [Memory](/broker/memory) | 支持 | 支持 | 内存队列，适合开发测试 |

### 定时器

| 连接器 | Source | Sink | 描述 |
|--------|--------|------|------|
| [Interval](/broker/cron) | 支持 | 不支持 | 固定间隔触发 |
| [Cron](/broker/cron) | 支持 | 不支持 | Cron 表达式触发 |

### 消息队列

| 连接器 | Source | Sink | 描述 |
|--------|--------|------|------|
| [Redis Streams](/broker/redis) | 支持 | 支持 | Redis Streams 消息队列，安装 `onestep-redis` |
| [RabbitMQ](/broker/rabbitmq) | 支持 | 支持 | RabbitMQ 队列，安装 `onestep-mq` |
| [AWS SQS](/broker/sqs) | 支持 | 支持 | AWS SQS 托管队列，安装 `onestep-sqs` |

### 数据库

| 连接器 | Source | Sink | 描述 |
|--------|--------|------|------|
| [MySQL](/broker/mysql) | 支持 | 支持 | 表队列/增量同步/表输出，安装 `onestep-mysql` |
| [Feishu Bitable](/broker/feishu-bitable) | 支持 | 支持 | 飞书多维表格增量同步/表输出，安装 `onestep-feishu-bitable` |

### Web

| 连接器 | Source | Sink | 描述 |
|--------|--------|------|------|
| [Webhook](/broker/webhook) | 支持 | 不支持 | HTTP 请求接收 |
| [HTTP Sink](/broker/http) | 不支持 | 支持 | HTTP JSON 请求输出 |

### 自定义

| 连接器 | Source | Sink | 描述 |
|--------|--------|------|------|
| [Custom](/broker/custom) | 支持 | 支持 | 实现任意数据源 |

## 选择指南

### 开发测试

```python
from onestep import MemoryQueue

source = MemoryQueue("test")
```

### 生产环境 - 分布式任务

```python
from onestep_rabbitmq import RabbitMQConnector

rmq = RabbitMQConnector("amqp://...")
source = rmq.queue("jobs")
```

### 生产环境 - 云原生

```python
from onestep_sqs import SQSConnector

sqs = SQSConnector(region_name="us-east-1")
source = sqs.queue("https://sqs...")
```

### 数据库驱动

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
from onestep import HttpSink, WebhookSource

# 接收外部系统推送
source = WebhookSource(path="/webhooks/github")

# 把处理结果发送到外部 HTTP 端点
sink = HttpSink("notify", url="https://example.com/hooks/events")
```

## YAML 配置

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

YAML 会通过已安装插件注册资源类型。使用 `rabbitmq`、`redis_stream`、`sqs_queue`、`mysql_table_queue` 或 `feishu_bitable_*` 前，先把对应插件安装到 worker 环境。

## 自定义 Source/Sink

参考 [Custom Broker](/broker/custom) 实现自定义数据源。

## 下一步

- [Memory](/broker/memory) - 内存队列
- [RabbitMQ](/broker/rabbitmq) - RabbitMQ 队列
- [Feishu Bitable](/broker/feishu-bitable) - 飞书多维表格同步
- [MySQL](/broker/mysql) - MySQL 集成
- [Webhook](/broker/webhook) - HTTP 接收
- [HTTP Sink](/broker/http) - HTTP 输出
- [Custom](/broker/custom) - 自定义实现

---
title: 快速开始 | 指南
---

# 快速开始

<project-info />

**onestep** 是一个轻量级异步任务运行时，帮助您轻松实现分布式任务调度。

## 安装

::: code-group

```bash [pip]
pip install onestep
```

```bash [poetry]
poetry add onestep
```

:::

### 可选依赖

::: code-group

```bash [MySQL]
pip install 'onestep[mysql]'
```

```bash [RabbitMQ]
pip install 'onestep[rabbitmq]'
```

```bash [AWS SQS]
pip install 'onestep[sqs]'
```

```bash [全部]
pip install 'onestep[all]'
```

:::

## 5 分钟快速上手

### 1. 创建任务

```python
# tasks.py
from onestep import IntervalSource, OneStepApp

app = OneStepApp("demo")


@app.task(source=IntervalSource.every(seconds=10, immediate=True))
async def hello_task(ctx, _):
    print(f"Hello from onestep! Time: {ctx.current.meta.get('scheduled_at')}")


if __name__ == "__main__":
    app.run()
```

### 2. 运行任务

```bash
# 直接运行
python tasks.py

# 或使用 CLI
onestep run tasks:app
```

### 3. 查看输出

```
Hello from onestep! Time: 2024-01-15T10:30:00+08:00
Hello from onestep! Time: 2024-01-15T10:30:10+08:00
Hello from onestep! Time: 2024-01-15T10:30:20+08:00
...
```

## 核心概念

### OneStepApp

任务注册和生命周期管理器：

```python
app = OneStepApp("my-app")
```

### Source

数据输入源（队列、定时器、Webhook 等）：

```python
from onestep import IntervalSource, MemoryQueue, CronSource

# 定时器
source = IntervalSource.every(minutes=5)

# 内存队列
source = MemoryQueue("incoming")

# Cron
source = CronSource("0 * * * *")
```

### Sink

数据输出目标：

```python
from onestep import MemoryQueue, RabbitMQConnector

# 内存队列
sink = MemoryQueue("output")

# RabbitMQ
sink = RabbitMQConnector("amqp://...").queue("results")
```

### Task

任务定义：

```python
@app.task(source=source, emit=sink, concurrency=4)
async def my_task(ctx, item):
    # ctx: 上下文
    # item: 消息体
    return {"processed": item}
```

## 常用场景

### 定时任务

```python
from onestep import CronSource

@app.task(source=CronSource("0 9 * * *", timezone="Asia/Shanghai"))
async def daily_report(ctx, _):
    # 每天上午 9 点执行
    ...
```

### 消息队列处理

```python
from onestep import RabbitMQConnector

rmq = RabbitMQConnector("amqp://guest:guest@localhost/")

@app.task(source=rmq.queue("jobs"), concurrency=8)
async def process_job(ctx, item):
    # 处理队列消息
    ...
```

### Webhook 接收

```python
from onestep import WebhookSource

@app.task(source=WebhookSource(path="/webhook", port=8080))
async def handle_webhook(ctx, event):
    # 处理 HTTP 请求
    return {"status": "ok"}
```

### 数据库增量同步

```python
from onestep import MySQLConnector

db = MySQLConnector("mysql+pymysql://...")

@app.task(
    source=db.incremental(
        table="users",
        key="id",
        cursor=("updated_at", "id")
    )
)
async def sync_user(ctx, row):
    # 增量同步用户数据
    ...
```

## CLI 命令

```bash
# 检查配置
onestep check tasks:app

# 运行应用
onestep run tasks:app

# 简写
onestep tasks:app

# 使用 JSON 输出（适合 CI）
onestep check --json tasks:app
```

## YAML 配置

```yaml
# worker.yaml
app:
  name: my-app

connectors:
  timer:
    type: interval
    seconds: 30
    immediate: true
  
  output:
    type: memory

tasks:
  - name: my_task
    source: timer
    emit: output
    handler:
      ref: tasks:my_task
```

运行：

```bash
onestep run worker.yaml
```

## 下一步

- [入门教程](/guide/tutorial) - 详细教程
- [功能特性](/guide/features) - 完整功能列表
- [RabbitMQ](/broker/rabbitmq) - 分布式队列
- [MySQL](/broker/mysql) - 数据库集成
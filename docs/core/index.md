---
title: 核心概念 | 核心
outline: deep
---

# 核心概念

## 架构概览

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│   Source    │ ──► │    Task     │ ──► │    Sink     │
│  (数据输入)  │     │  (任务处理)  │     │  (数据输出)  │
└─────────────┘     └─────────────┘     └─────────────┘
```

## OneStepApp

应用入口，负责任务注册和生命周期管理：

```python
from onestep import OneStepApp

app = OneStepApp(
    "my-app",                    # 应用名称
    config={"key": "value"},     # 配置
    state=InMemoryStateStore(),  # 状态存储
    shutdown_timeout_s=30.0,     # 关闭超时
)
```

### 任务注册

```python
@app.task(source=..., emit=...)
async def my_task(ctx, item):
    ...
```

### 事件监听

```python
app.on_event(InMemoryMetrics())
app.on_event(StructuredEventLogger())
```

### 生命周期钩子

```python
@app.on_startup
async def bootstrap(app):
    ...

@app.on_shutdown
async def cleanup(app):
    ...
```

## Source

数据输入源，负责获取消息：

```python
from onestep import CronSource, IntervalSource, MemoryQueue, WebhookSource
from onestep_mysql import MySQLConnector
from onestep_rabbitmq import RabbitMQConnector

# 内存队列
source = MemoryQueue("incoming")

# 定时器
source = IntervalSource.every(minutes=5)

# Cron
source = CronSource("0 * * * *")

# Webhook
source = WebhookSource(path="/webhook")

# RabbitMQ
source = RabbitMQConnector("amqp://...").queue("jobs")

# MySQL
source = MySQLConnector("mysql://...").table_queue("tasks")
```

RabbitMQ、MySQL、Redis Streams、AWS SQS 和 Feishu Bitable 由插件包提供，安装后从对应插件模块导入 Python API。

### 自定义 Source

```python
from onestep import Source, Delivery

class MySource(Source):
    async def fetch(self) -> list[Delivery]:
        # 获取消息
        ...
    
    async def ack(self, delivery: Delivery):
        # 确认消息
        ...
```

## Sink

数据输出目标，负责发布消息：

```python
from onestep import MemoryQueue
from onestep_mysql import MySQLConnector
from onestep_rabbitmq import RabbitMQConnector

# 内存队列
sink = MemoryQueue("output")

# RabbitMQ
sink = RabbitMQConnector("amqp://...").queue("results")

# MySQL
sink = MySQLConnector("mysql://...").table_sink("results")
```

### 自定义 Sink

```python
from onestep import Sink

class MySink(Sink):
    async def publish(self, body, meta=None):
        # 发布消息
        ...
```

## Delivery

消息传递对象：

运行时从 `Source.fetch()` 拿到 `Delivery`，再把 `delivery.payload` 传给任务函数。自定义 Source 需要实现 `ack()`、`retry()` 和 `fail()`，内置连接器已经处理好确认、重试和失败语义。

## Task Context

任务执行上下文：

```python
@app.task(source=...)
async def my_task(ctx, item):
    # ctx.app - OneStepApp 实例
    # ctx.config - 应用配置
    # ctx.state - 任务状态
    # ctx.current - 当前执行信息
    ...
```

### 配置访问

```python
app = OneStepApp("demo", config={"region": "cn"})


@app.task(source=...)
async def task(ctx, item):
    region = ctx.config["region"]
```

### 状态管理

```python
@app.task(source=...)
async def task(ctx, item):
    count = await ctx.state.get("count", 0)
    await ctx.state.set("count", count + 1)
```

## 消息流转

### 基本流转

```python
@app.task(source=source, emit=sink)
async def process(ctx, item):
    return {"result": item}  # 返回值发送到 sink
```

### 多阶段流转

```python
queue1 = MemoryQueue("stage1")
queue2 = MemoryQueue("stage2")


@app.task(source=MemoryQueue("input"), emit=queue1)
async def stage1(ctx, item):
    return item * 2


@app.task(source=queue1, emit=queue2)
async def stage2(ctx, item):
    return item + 1


@app.task(source=queue2)
async def final(ctx, item):
    print(f"结果：{item}")
```

## Managed Execution

onestep 1.9 新增了受管执行（Managed Execution）模式，把任务状态、结果和租约持久化到数据库（当前仅 PostgreSQL），适合长时间运行的任务（如 AI Agent 调用）。

### 架构

```
FastAPI / Gateway                  Worker
┌──────────────┐                  ┌──────────────────┐
│ExecutionClient│  ──submit──►    │ExecutionBackend   │◄── PostgresExecutionSource
│  .submit()    │                  │  (PostgreSQL)     │     .claim()
│  .get()       │                  │                   │     heartbeat/complete
│  .cancel()    │                  └───────────────────┘
└──────────────┘
```

### 提交执行

```python
from onestep import ExecutionClient
from onestep_postgres import PostgresExecutionBackend

backend = PostgresExecutionBackend(
    dsn="postgresql+psycopg://app:secret@db/app",
    auto_create=True,
)
client = ExecutionClient(backend, namespace="agent-api")

async with client:
    execution = await client.submit(
        "run_agent",
        {"prompt": "..."},
        idempotency_key=request_id,
    )
    # 轮询结果
    result = await execution.result()
```

### Worker 消费

```python
from onestep_postgres import PostgresExecutionSource

source = PostgresExecutionSource(
    dsn="postgresql+psycopg://app:secret@db/app",
    namespace="agent-api",
    task_names=("run_agent",),
    worker_id="agent-worker-1",
)
```

每个 execution source 只能配置一个任务名，且必须与绑定该 source 的 app task 名一致。

### 状态机

任务状态包括 `queued` → `running` → `succeeded` / `failed` / `cancelled` / `expired`，以及中间状态 `retrying` 和 `cancel_requested`。`Execution` 是不可变快照；需要最新状态时重新调用 `get()` 或 `list()`。

### 租约与可靠性

执行使用租约（lease）保证 at-least-once：worker 通过 `heartbeat()` 续约，过期租约由 `claim()` 回收。系统是协作式取消；handler 的外部副作用仍需业务幂等。

详细说明见 [PostgreSQL Tracked Execution](/broker/postgres-execution) 和 [核心可靠性](/core-reliability)。

## 错误处理

### 重试

```python
from onestep import MaxAttempts

@app.task(
    source=...,
    retry=MaxAttempts(max_attempts=3, delay_s=1.0)
)
async def might_fail(ctx, item):
    ...
```

### 死信队列

```python
@app.task(
    source=main_queue,
    dead_letter=dead_letter_queue
)
async def risky_task(ctx, item):
    ...
```

### 超时

```python
@app.task(source=..., timeout_s=30.0)
async def long_task(ctx, item):
    ...
```

## 运行模式

### 直接运行

```python
if __name__ == "__main__":
    app.run()
```

### CLI 运行

```bash
onestep run module:app
```

### 异步运行

```python
import asyncio

async def main():
    await app.serve()

asyncio.run(main())
```

## 下一步

- [Connector](/core/connector) - 连接器详解
- [Retry](/core/retry) - 重试策略
- [Middleware](/core/middleware) - 事件钩子

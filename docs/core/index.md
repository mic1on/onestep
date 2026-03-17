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

```python
delivery = Delivery(
    body={"data": "..."},     # 消息体
    meta={"key": "value"}     # 元数据
)
```

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
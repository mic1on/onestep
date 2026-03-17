---
title: 功能特性 | 指南
outline: deep
---

# 功能特性

## 核心特性

### 🎯 简单直观

```python
from onestep import OneStepApp, IntervalSource

app = OneStepApp("demo")


@app.task(source=IntervalSource.every(seconds=30))
async def my_task(ctx, item):
    print("处理:", item)
```

### 🔌 多种数据源

| 类型 | Source | Sink | 描述 |
|------|--------|------|------|
| 内存队列 | ✅ | ✅ | 开发测试 |
| 定时器 | ✅ | ❌ | Cron/Interval |
| Webhook | ✅ | ❌ | HTTP 接收 |
| RabbitMQ | ✅ | ✅ | 分布式队列 |
| AWS SQS | ✅ | ✅ | 云队列 |
| MySQL | ✅ | ✅ | 表队列/增量同步 |
| 自定义 | ✅ | ✅ | 任意数据源 |

### 🔄 灵活流转

```python
# 自由组合不同 Broker
@app.task(
    source=CronSource("0 * * * *"),  # 定时触发
    emit=RabbitMQConnector("amqp://...").queue("results")  # 发到 MQ
)
async def scheduled_to_mq(ctx, _):
    return {"data": "..."}


@app.task(
    source=RabbitMQConnector("amqp://...").queue("results"),  # 从 MQ 消费
    emit=MySQLConnector("mysql://...").table_sink("results")  # 写入数据库
)
async def mq_to_db(ctx, item):
    return item
```

### ⚡ 并发控制

```python
# 控制并发数
@app.task(source=..., concurrency=4)
async def task1(ctx, item):
    ...

# 高并发处理
@app.task(source=..., concurrency=64)
async def task2(ctx, item):
    ...
```

### 🛡️ 重试机制

```python
from onestep import MaxAttempts

# 最多重试 3 次，每次间隔 1 秒
@app.task(
    source=...,
    retry=MaxAttempts(max_attempts=3, delay_s=1.0)
)
async def might_fail(ctx, item):
    ...
```

### 💀 死信队列

```python
# 失败消息发送到死信队列
@app.task(
    source=main_queue,
    dead_letter=dead_letter_queue,
    retry=MaxAttempts(max_attempts=3)
)
async def risky_task(ctx, item):
    ...


# 处理死信
@app.task(source=dead_letter_queue)
async def handle_dead_letter(ctx, item):
    print(f"死信：{item}")
```

### ⏱️ 执行超时

```python
# 30 秒超时
@app.task(source=..., timeout_s=30.0)
async def long_task(ctx, item):
    await asyncio.sleep(60)  # 会被取消
```

### 📊 事件监听

```python
from onestep import InMemoryMetrics, StructuredEventLogger

app = OneStepApp("demo")

# 内置事件处理器
app.on_event(InMemoryMetrics())
app.on_event(StructuredEventLogger())

# 自定义事件处理器
class MyHandler(EventHandler):
    def on_event(self, event):
        if event.kind == "succeeded":
            print(f"成功：{event.task_name}")

app.on_event(MyHandler())
```

### 🔧 状态管理

```python
from onestep import InMemoryStateStore

app = OneStepApp("demo", state=InMemoryStateStore())


@app.task(source=...)
async def track_runs(ctx, item):
    runs = await ctx.state.get("runs", 0)
    await ctx.state.set("runs", runs + 1)
    print(f"第 {runs + 1} 次运行")
```

### 🚀 生命周期钩子

```python
@app.on_startup
async def bootstrap(app):
    print("应用启动")
    # 初始化资源

@app.on_shutdown
async def cleanup(app):
    print("应用关闭")
    # 清理资源
```

### 📝 YAML 配置

```yaml
app:
  name: my-app

connectors:
  timer:
    type: interval
    minutes: 5
  queue:
    type: rabbitmq_queue
    connector: rmq
    queue: "jobs"

tasks:
  - name: process_jobs
    source: timer
    emit: queue
    retry:
      type: max_attempts
      max_attempts: 3
```

## 高级特性

### 任务编排

```python
# 流水线处理
input_queue = MemoryQueue("input")
stage1_out = MemoryQueue("stage1")
stage2_out = MemoryQueue("stage2")


@app.task(source=input_queue, emit=stage1_out)
async def stage1(ctx, item):
    return item * 2


@app.task(source=stage1_out, emit=stage2_out)
async def stage2(ctx, item):
    return item + 1


@app.task(source=stage2_out)
async def final(ctx, item):
    print(f"结果：{item}")
```

### 优雅关闭

```python
app = OneStepApp("demo", shutdown_timeout_s=30.0)


@app.task(source=...)
async def task(ctx, item):
    # 30 秒内未完成的任务会被取消
    await process(item)


# 请求关闭
@app.task(source=...)
async def shutdown_trigger(ctx, item):
    ctx.app.request_shutdown()
```

### 配置管理

```python
app = OneStepApp(
    "my-app",
    config={
        "region": "cn",
        "debug": True,
        "batch_size": 100
    }
)


@app.task(source=...)
async def task(ctx, item):
    region = ctx.config["region"]
    debug = ctx.config.get("debug", False)
```

### Control Plane 集成

```python
from onestep import ControlPlaneReporter, ControlPlaneReporterConfig

app = OneStepApp("my-app")
reporter = ControlPlaneReporter(
    ControlPlaneReporterConfig.from_env(app_name=app.name)
)
reporter.attach(app)

# 自动推送：
# - 拓扑同步
# - 心跳
# - 指标
# - 事件
```

## 对比 0.5.x

| 特性 | 0.5.x | 1.0.0 |
|------|-------|-------|
| 装饰器 | `@step` | `@app.task` |
| 消息来源 | `from_broker` | `source` |
| 消息输出 | `to_broker` | `emit` |
| 并发控制 | `workers` | `concurrency` |
| 启动方式 | `step.start()` | `app.run()` / CLI |
| 重试策略 | `TimesRetry` 等 | `MaxAttempts` |
| 中间件 | `BaseMiddleware` | 事件钩子 |
| 状态 | 无 | `ctx.state` |
| 配置 | 无 | `ctx.config` |
| 生命周期 | 有限 | `@app.on_startup/shutdown` |

## 下一步

- [入门教程](/guide/tutorial) - 快速上手
- [CLI 部署](/guide/deploy) - 生产环境部署
- [RabbitMQ](/broker/rabbitmq) - 分布式队列
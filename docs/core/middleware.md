---
title: Middleware | 核心
outline: deep
---

# Middleware

onestep 1.0.0 使用**事件钩子**替代传统的中间件模式，提供更清晰的生命周期控制。

## 事件钩子

使用 `@app.on_event` 注册事件处理器：

```python
from onestep import InMemoryMetrics, OneStepApp, StructuredEventLogger

app = OneStepApp("demo")
metrics = InMemoryMetrics()

# 注册内置的事件处理器
app.on_event(metrics)
app.on_event(StructuredEventLogger())


@app.task(source=...)
async def my_task(ctx, item):
    return item
```

## 执行事件

任务执行过程中会发出以下事件：

| 事件 | 触发时机 |
|------|----------|
| `fetched` | 从 Source 获取消息 |
| `started` | 开始执行任务 |
| `succeeded` | 任务执行成功 |
| `retried` | 任务重试 |
| `failed` | 任务最终失败 |
| `dead_lettered` | 消息进入死信队列 |
| `cancelled` | 任务被取消 |

## 自定义事件处理器

实现自定义事件处理器：

```python
from onestep import TaskEvent, EventHandler

class MyEventHandler(EventHandler):
    def on_event(self, event: TaskEvent):
        if event.kind == "succeeded":
            print(f"任务成功: {event.task_name}, 耗时: {event.duration_s:.2f}s")
        elif event.kind == "failed":
            print(f"任务失败: {event.task_name}, 原因: {event.failure.message}")
        
    def on_error(self, error: Exception):
        # 处理事件处理器本身的错误
        print(f"事件处理器错误: {error}")


app.on_event(MyEventHandler())
```

## 内置事件处理器

### InMemoryMetrics

内存指标收集器：

```python
from onestep import InMemoryMetrics

metrics = InMemoryMetrics()
app.on_event(metrics)

# 获取指标快照
snapshot = metrics.snapshot()
print(snapshot["kinds"])  # 各类事件计数
```

### StructuredEventLogger

结构化日志输出：

```python
from onestep import StructuredEventLogger

app.on_event(StructuredEventLogger())
```

输出包含字段：`event_kind`, `app_name`, `task_name`, `source_name`, `attempts`, `duration_s`, `failure_kind`

## 生命周期钩子

### @app.on_startup

应用启动时执行：

```python
@app.on_startup
async def bootstrap(app):
    print("应用启动")
    # 初始化资源、预发布消息等
```

### @app.on_shutdown

应用关闭时执行：

```python
@app.on_shutdown
async def cleanup(app):
    print("应用关闭")
    # 清理资源
```

## 任务状态

每个任务可以维护独立的状态：

```python
from onestep import InMemoryStateStore, OneStepApp

app = OneStepApp("state-demo", state=InMemoryStateStore())


@app.task(source=...)
async def track_runs(ctx, item):
    # 获取状态
    runs = await ctx.state.get("runs", 0)
    
    # 更新状态
    await ctx.state.set("runs", runs + 1)
    
    print(f"已处理 {runs + 1} 条消息")
```

## 配置访问

通过 `ctx.config` 访问应用配置：

```python
app = OneStepApp("demo", config={"region": "cn", "debug": True})


@app.task(source=...)
async def my_task(ctx, item):
    region = ctx.config["region"]
    debug = ctx.config.get("debug", False)
    ...
```

## 从 0.5.x 迁移

旧版中间件：

```python
# 0.5.x
class MyMiddleware(BaseMiddleware):
    def before_consume(self, step, message, *args, **kwargs):
        ...

@step(from_broker=..., middlewares=[MyMiddleware()])
def task(message):
    ...
```

新版事件钩子：

```python
# 1.0.0
class MyEventHandler(EventHandler):
    def on_event(self, event: TaskEvent):
        if event.kind == "started":
            # 相当于 before_consume
            ...
        elif event.kind == "succeeded":
            # 相当于 after_consume
            ...

app.on_event(MyEventHandler())

@app.task(source=...)
async def task(ctx, item):
    ...
```

消息去重逻辑现在应该在任务处理器中实现，或使用数据库的唯一约束。
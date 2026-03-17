---
title: Cron & Interval | Broker
outline: deep
---

# Cron & Interval

定时任务触发器，支持 Cron 表达式和固定间隔两种模式。

## IntervalSource

固定间隔触发，适合不关心具体时间点的场景。

### 基本用法

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("interval-demo")


@app.task(source=IntervalSource.every(hours=1, immediate=True))
async def hourly_task(ctx, _):
    print("每小时执行一次")


if __name__ == "__main__":
    app.run()
```

### 配置选项

```python
source = IntervalSource.every(
    seconds=30,           # 间隔秒数
    minutes=5,            # 或分钟
    hours=1,              # 或小时
    immediate=True,       # 启动时立即执行一次
    overlap="skip",       # 重叠处理策略
    payload={"job": "x"}, # 自定义消息体
)
```

### 时间单位

```python
# 每 30 秒
IntervalSource.every(seconds=30)

# 每 5 分钟
IntervalSource.every(minutes=5)

# 每 2 小时
IntervalSource.every(hours=2)

# 组合使用
IntervalSource.every(hours=1, minutes=30)  # 每 1.5 小时
```

### overlap 重叠策略

当上次执行尚未完成时：

```python
# allow: 允许并发执行
@app.task(source=IntervalSource.every(seconds=10, overlap="allow"))
async def task1(ctx, _):
    await asyncio.sleep(15)  # 执行时间 > 间隔
    # 结果：每次都会启动新实例，可能并发多个

# skip: 跳过本次执行
@app.task(source=IntervalSource.every(seconds=10, overlap="skip"))
async def task2(ctx, _):
    await asyncio.sleep(15)
    # 结果：错过的触发被跳过

# queue: 排队执行
@app.task(source=IntervalSource.every(seconds=10, overlap="queue"))
async def task3(ctx, _):
    await asyncio.sleep(15)
    # 结果：错过的触发排队，依次执行
```

## CronSource

基于 Cron 表达式触发，适合特定时间点执行。

### 基本用法

```python
from onestep import CronSource, OneStepApp

app = OneStepApp("cron-demo")


@app.task(source=CronSource("0 * * * *", timezone="Asia/Shanghai"))
async def hourly_at_zero(ctx, _):
    print("每小时整点执行")


if __name__ == "__main__":
    app.run()
```

### Cron 表达式

标准 5 字段格式：

```
┌───────────── 分钟 (0-59)
│ ┌───────────── 小时 (0-23)
│ │ ┌───────────── 日期 (1-31)
│ │ │ ┌───────────── 月份 (1-12)
│ │ │ │ ┌───────────── 星期几 (0-6, 0=周日)
│ │ │ │ │
* * * * *
```

示例：

```python
# 每小时整点
CronSource("0 * * * *")

# 每天凌晨 2 点
CronSource("0 2 * * *")

# 每周一上午 9 点
CronSource("0 9 * * 1")

# 每月 1 号凌晨 0 点
CronSource("0 0 1 * *")

# 每 15 分钟
CronSource("*/15 * * * *")

# 工作日上午 9 点
CronSource("0 9 * * 1-5")
```

### 别名

支持常用别名：

```python
CronSource("@hourly")   # 每小时
CronSource("@daily")    # 每天 0 点
CronSource("@weekly")   # 每周日 0 点
CronSource("@monthly")  # 每月 1 号 0 点
CronSource("@yearly")   # 每年 1 月 1 日 0 点
```

### 时区

```python
# 使用时区
CronSource("0 9 * * *", timezone="Asia/Shanghai")
CronSource("0 9 * * *", timezone="America/New_York")
CronSource("0 9 * * *", timezone="UTC")
```

### 配置选项

```python
source = CronSource(
    "0 9 * * 1-5",       # Cron 表达式
    timezone="Asia/Shanghai",  # 时区
    overlap="skip",      # 重叠策略
    immediate=False,     # 启动时立即执行
    payload={"type": "report"},  # 自定义消息体
)
```

## 上下文信息

定时任务可通过 `ctx.current.meta` 获取调度信息：

```python
@app.task(source=CronSource("0 * * * *"))
async def scheduled_task(ctx, _):
    scheduled_at = ctx.current.meta["scheduled_at"]
    print(f"计划执行时间: {scheduled_at}")
```

## 示例：数据同步

```python
from onestep import CronSource, MySQLConnector, OneStepApp, RabbitMQConnector

app = OneStepApp("data-sync")
db = MySQLConnector("mysql+pymysql://...")
rmq = RabbitMQConnector("amqp://...")


# 每天凌晨 2 点同步用户数据
@app.task(source=CronSource("0 2 * * *", timezone="Asia/Shanghai"))
async def sync_users(ctx, _):
    print("开始同步用户数据...")
    # 业务逻辑
    ...


# 每 15 分钟同步订单状态
@app.task(
    source=IntervalSource.every(minutes=15, immediate=True),
    emit=rmq.queue("order-sync"),
)
async def sync_orders(ctx, _):
    print("检查订单状态...")
    ...


if __name__ == "__main__":
    app.run()
```

## YAML 配置

```yaml
connectors:
  # Interval
  tick:
    type: interval
    seconds: 30
    immediate: true
    overlap: skip
  
  # Cron
  daily:
    type: cron
    expression: "0 2 * * *"
    timezone: "Asia/Shanghai"

tasks:
  - name: daily_sync
    source: daily
    handler:
      ref: myapp.tasks:daily_sync
```

## 最佳实践

### 1. 选择合适的触发器

- **IntervalSource**: 不关心具体时间点，只关心间隔
- **CronSource**: 需要在特定时间点执行（如每天凌晨）

### 2. 时区注意

```python
# 明确指定时区，避免服务器时区不一致
CronSource("0 9 * * *", timezone="Asia/Shanghai")
```

### 3. 重叠处理

长时间任务务必设置 `overlap`：

```python
# 推荐：skip 或 queue
@app.task(source=IntervalSource.every(minutes=5, overlap="skip"))
async def long_task(ctx, _):
    await asyncio.sleep(600)  # 10 分钟
```

### 4. immediate 选项

```python
# 启动时立即执行一次（适合开发调试）
IntervalSource.every(hours=1, immediate=True)

# 启动后等待第一个间隔（生产推荐）
IntervalSource.every(hours=1, immediate=False)
```
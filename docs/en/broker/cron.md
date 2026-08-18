---
title: Cron & Interval | Broker
outline: deep
---

# Cron & Interval

Scheduled task triggers supporting both Cron expressions and fixed interval modes.

## IntervalSource

Fixed interval triggers, suitable for scenarios where exact time doesn't matter.

### Basic Usage

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("interval-demo")


@app.task(source=IntervalSource.every(hours=1, immediate=True))
async def hourly_task(ctx, _):
    print("Executes every hour")


if __name__ == "__main__":
    app.run()
```

### Configuration Options

```python
source = IntervalSource.every(
    seconds=30,           # Interval in seconds
    minutes=5,            # or minutes
    hours=1,              # or hours
    immediate=True,       # Execute once immediately on start
    overlap="skip",       # Overlap handling strategy
    payload={"job": "x"}, # Custom message body
)
```

### Time Units

```python
# Every 30 seconds
IntervalSource.every(seconds=30)

# Every 5 minutes
IntervalSource.every(minutes=5)

# Every 2 hours
IntervalSource.every(hours=2)

# Combined
IntervalSource.every(hours=1, minutes=30)  # Every 1.5 hours
```

### Overlap Strategy

When the previous execution hasn't completed:

```python
# allow: Permit concurrent execution
@app.task(source=IntervalSource.every(seconds=10, overlap="allow"))
async def task1(ctx, _):
    await asyncio.sleep(15)  # Execution time > interval
    # Result: New instance starts each time, may run multiple concurrently

# skip: Skip this execution
@app.task(source=IntervalSource.every(seconds=10, overlap="skip"))
async def task2(ctx, _):
    await asyncio.sleep(15)
    # Result: Missed triggers are skipped

# queue: Queue execution
@app.task(source=IntervalSource.every(seconds=10, overlap="queue"))
async def task3(ctx, _):
    await asyncio.sleep(15)
    # Result: Missed triggers are queued, executed sequentially
```

## CronSource

Based on Cron expressions, suitable for execution at specific times.

### Basic Usage

```python
from onestep import CronSource, OneStepApp

app = OneStepApp("cron-demo")


@app.task(source=CronSource("0 * * * *", timezone="Asia/Shanghai"))
async def hourly_at_zero(ctx, _):
    print("Executes at the start of every hour")


if __name__ == "__main__":
    app.run()
```

### Cron Expression

Standard 5-field format:

```
┌───────────── minute (0-59)
│ ┌───────────── hour (0-23)
│ │ ┌───────────── day of month (1-31)
│ │ │ ┌───────────── month (1-12)
│ │ │ │ ┌───────────── day of week (0-6, 0=Sunday)
│ │ │ │ │
* * * * *
```

Examples:

```python
# Every hour at :00
CronSource("0 * * * *")

# Daily at 2 AM
CronSource("0 2 * * *")

# Every Monday at 9 AM
CronSource("0 9 * * 1")

# 1st of every month at midnight
CronSource("0 0 1 * *")

# Every 15 minutes
CronSource("*/15 * * * *")

# Weekdays at 9 AM
CronSource("0 9 * * 1-5")
```

### Aliases

Supports common aliases:

```python
CronSource("@hourly")   # Every hour
CronSource("@daily")    # Daily at midnight
CronSource("@weekly")   # Weekly on Sunday at midnight
CronSource("@monthly")  # Monthly on the 1st at midnight
CronSource("@yearly")   # Yearly on Jan 1st at midnight
```

### Timezone

```python
# With timezone
CronSource("0 9 * * *", timezone="Asia/Shanghai")
CronSource("0 9 * * *", timezone="America/New_York")
CronSource("0 9 * * *", timezone="UTC")
```

### Configuration Options

```python
source = CronSource(
    "0 9 * * 1-5",              # Cron expression
    timezone="Asia/Shanghai",    # Timezone
    overlap="skip",              # Overlap strategy
    immediate=False,             # Execute immediately on start
    payload={"type": "report"},  # Custom message body
)
```

## Context Info

Scheduled tasks can access scheduling info via `ctx.current.meta`:

```python
@app.task(source=CronSource("0 * * * *"))
async def scheduled_task(ctx, _):
    scheduled_at = ctx.current.meta["scheduled_at"]
    print(f"Scheduled time: {scheduled_at}")
```

## Example: Data Sync

```python
from onestep import CronSource, OneStepApp
from onestep_mysql import MySQLConnector
from onestep_rabbitmq import RabbitMQConnector

app = OneStepApp("data-sync")
db = MySQLConnector("mysql+pymysql://...")
rmq = RabbitMQConnector("amqp://...")


# Sync user data daily at 2 AM
@app.task(source=CronSource("0 2 * * *", timezone="Asia/Shanghai"))
async def sync_users(ctx, _):
    print("Starting user data sync...")
    # Business logic
    ...


# Sync order status every 15 minutes
@app.task(
    source=IntervalSource.every(minutes=15, immediate=True),
    emit=rmq.queue("order-sync"),
)
async def sync_orders(ctx, _):
    print("Checking order status...")
    ...


if __name__ == "__main__":
    app.run()
```

## YAML Configuration

```yaml
resources:
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

## Best Practices

### 1. Choosing the Right Trigger

- **IntervalSource**: When you don't care about exact time, only the interval
- **CronSource**: When you need execution at specific times (e.g., daily at midnight)

### 2. Timezone Awareness

```python
# Explicitly specify timezone to avoid server timezone mismatches
CronSource("0 9 * * *", timezone="Asia/Shanghai")
```

### 3. Overlap Handling

Long-running tasks must set `overlap`:

```python
# Recommended: skip or queue
@app.task(source=IntervalSource.every(minutes=5, overlap="skip"))
async def long_task(ctx, _):
    await asyncio.sleep(600)  # 10 minutes
```

### 4. Immediate Option

```python
# Execute once immediately on start (suitable for dev/debug)
IntervalSource.every(hours=1, immediate=True)

# Wait for first interval (recommended for production)
IntervalSource.every(hours=1, immediate=False)
```

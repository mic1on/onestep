---
title: 快速开始 | 指南
outline: deep
---

# 快速开始

onestep 是一个轻量级 Python 异步任务运行时。它围绕 `OneStepApp`、`Source`、`Sink` 和任务处理函数组织代码，适合队列消费、定时同步、Webhook 接入和多阶段数据处理。

当前包版本为 `1.2.4`。文档站使用 VitePress `1.6.4`，这是 2026-05-09 npm `latest` 对应的稳定版本；`2.0.0-alpha.17` 仍在 `next` 标签下。

## 安装

::: code-group

```bash [pip]
pip install onestep
```

```bash [uv]
uv add onestep
```

```bash [poetry]
poetry add onestep
```

:::

按使用场景安装可选依赖：

::: code-group

```bash [YAML]
pip install 'onestep[yaml]'
```

```bash [MySQL]
pip install 'onestep[mysql]'
```

```bash [RabbitMQ]
pip install 'onestep[rabbitmq]'
```

```bash [Redis]
pip install 'onestep[redis]'
```

```bash [AWS SQS]
pip install 'onestep[sqs]'
```

```bash [全部]
pip install 'onestep[all]'
```

:::

## 第一个任务

创建 `tasks.py`：

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("demo")


@app.task(source=IntervalSource.every(seconds=10, immediate=True))
async def hello(ctx, _):
    scheduled_at = ctx.current.meta["scheduled_at"]
    print(f"hello from onestep: {scheduled_at}")


if __name__ == "__main__":
    app.run()
```

运行：

::: code-group

```bash [CLI]
onestep run tasks:app
```

```bash [Python]
python tasks.py
```

:::

生产环境建议使用 CLI，因为它可以在启动前检查目标：

```bash
onestep check tasks:app
onestep check --json tasks:app
onestep run tasks:app
```

`onestep tasks:app` 是 `onestep run tasks:app` 的简写。

## 处理队列消息

`MemoryQueue` 同时实现了 `Source` 和 `Sink`，适合本地开发和测试。

```python
import asyncio

from onestep import MemoryQueue, OneStepApp

app = OneStepApp("memory-pipeline")
source = MemoryQueue("incoming")
sink = MemoryQueue("processed")


@app.task(source=source, emit=sink, concurrency=2)
async def double(ctx, item):
    return {"value": item["value"] * 2}


async def main():
    await source.publish({"value": 21})
    await app.serve()


asyncio.run(main())
```

真实部署时通常把 `MemoryQueue` 换成外部系统连接器，例如 RabbitMQ、Redis Streams、AWS SQS 或 MySQL。

## 使用外部连接器

```python
from onestep import MySQLConnector, OneStepApp, RabbitMQConnector

app = OneStepApp("orders")
rmq = RabbitMQConnector("amqp://guest:guest@localhost/")
db = MySQLConnector("mysql+pymysql://user:pass@localhost/app")

jobs = rmq.queue("orders")
rows = db.table_sink(table="processed_orders", mode="upsert", keys=("id",))


@app.task(source=jobs, emit=rows, concurrency=8)
async def process_order(ctx, order):
    return {
        "id": order["id"],
        "status": "processed",
    }
```

## YAML 配置

安装 `onestep[yaml]` 后，可以把运行时资源和任务拓扑写进 `worker.yaml`：

```yaml
app:
  name: billing-sync

resources:
  tick:
    type: interval
    minutes: 5
    immediate: true

tasks:
  - name: sync_billing
    source: tick
    handler:
      ref: your_package.handlers:sync_billing
```

检查并运行：

```bash
onestep check --strict worker.yaml
onestep run worker.yaml
```

`resources` 是推荐写法。旧的 `connectors`、`sources` 和 `sinks` 仍可读取，但新文档统一使用 `resources`。

## 下一步

- [入门教程](/guide/tutorial) 通过几个完整例子串起核心概念。
- [连接器概览](/broker/) 帮你选择 Memory、Cron、Webhook、RabbitMQ、Redis、SQS 或 MySQL。
- [YAML 任务定义](/yaml-task-definition) 说明完整配置字段和严格校验。
- [生产部署](/guide/deploy) 介绍 CLI、systemd 和持久化状态。

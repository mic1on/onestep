---
title: 入门教程 | 指南
---

# 入门教程

本教程将帮助你快速上手 onestep 1.0.0。

## 核心概念

onestep 1.0.0 围绕四个核心概念构建：

- **OneStepApp**: 任务注册和生命周期管理器
- **Source**: 从队列或轮询后端获取数据
- **Sink**: 发布处理后的数据
- **Delivery**: 单个获取的消息项，支持 `ack/retry/fail`

## 基础示例：内存队列

最简单的例子，使用内存队列：

```python
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("demo")
source = MemoryQueue("incoming")
sink = MemoryQueue("processed")


@app.task(source=source, emit=sink, concurrency=4)
async def double(ctx, item):
    return {"value": item["value"] * 2}


async def main():
    await source.publish({"value": 21})
    await app.serve()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## 定时任务

使用 `IntervalSource` 实现定时执行：

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, _):
    print("syncing billing data")


if __name__ == "__main__":
    app.run()
```

`overlap` 参数控制上次执行仍在进行时的行为：
- `allow`: 立即开始另一次执行
- `skip`: 跳过错过的触发
- `queue`: 将错过的触发排队，依次执行

## Cron 定时任务

使用 `CronSource` 基于墙钟时间调度：

```python
from onestep import CronSource, OneStepApp

app = OneStepApp("hourly-sync")


@app.task(source=CronSource("0 * * * *", timezone="Asia/Shanghai", overlap="skip"))
async def sync_hourly(ctx, _):
    print("running at:", ctx.current.meta["scheduled_at"])


if __name__ == "__main__":
    app.run()
```

支持标准 5 字段 cron 表达式和别名：`@hourly`, `@daily`, `@weekly`, `@monthly`, `@yearly`

## Webhook 接收

使用 `WebhookSource` 接收外部 HTTP 请求：

```python
from onestep import BearerAuth, MemoryQueue, OneStepApp, WebhookSource

app = OneStepApp("webhook-demo")
jobs = MemoryQueue("jobs")


@app.task(
    source=WebhookSource(
        path="/webhooks/github",
        methods=("POST",),
        host="127.0.0.1",
        port=8080,
        auth=BearerAuth("your-secret-token"),
    ),
    emit=jobs,
)
async def ingest_github(ctx, event):
    return {
        "event": event["headers"].get("x-github-event"),
        "payload": event["body"],
    }


if __name__ == "__main__":
    app.run()
```

## 爬虫示例：多阶段处理

展示列表页 -> 详情页的爬虫场景：

```python
import httpx
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("spider-demo")

# 定义队列
page_queue = MemoryQueue("pages")
list_queue = MemoryQueue("list")
detail_queue = MemoryQueue("detail")


@app.task(source=page_queue, emit=list_queue, concurrency=2)
async def crawl_list(ctx, page):
    """抓取列表页，提取 URL"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(f"https://httpbin.org/anything/{page}")
        url = resp.json().get("url")
        return url


@app.task(source=list_queue, emit=detail_queue, concurrency=4)
async def crawl_detail(ctx, url):
    """抓取详情页"""
    async with httpx.AsyncClient() as client:
        resp = await client.get(url)
        return resp.json()


async def main():
    # 模拟 10 个页面任务
    for i in range(1, 11):
        await page_queue.publish(i)
    
    await app.serve()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## 使用 CLI 运行

推荐使用 CLI 作为部署入口点：

```python
# tasks.py
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True))
async def sync_billing(ctx, _):
    print("syncing billing data")
```

运行：

```bash
# 检查配置
onestep check tasks:app

# 运行应用
onestep run tasks:app

# 或简写
onestep tasks:app
```

## YAML 配置

支持使用 YAML 文件定义应用：

```yaml
# worker.yaml
app:
  name: billing-sync

connectors:
  tick:
    type: interval
    minutes: 5
    immediate: true
  processed:
    type: memory

tasks:
  - name: sync_billing
    source: tick
    handler:
      ref: your_package.handlers:sync_billing
      params:
        region: cn
    emit: [processed]
    retry:
      type: max_attempts
      max_attempts: 3
      delay_s: 10
```

运行：

```bash
onestep check worker.yaml
onestep run worker.yaml
```

## 下一步

- [功能特性](/guide/features) - 了解所有支持的特性
- [RabbitMQ](/broker/rabbitmq) - 分布式消息队列
- [MySQL](/broker/mysql) - 数据库表队列和增量同步
- [CLI 部署](/guide/deploy) - 生产环境部署指南
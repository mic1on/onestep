---
title: Redis Streams | Broker
outline: deep
---

# Redis Streams

Redis Streams 是 Redis 5.0 引入的数据结构，适合轻量级、可靠的消息队列场景。

## 安装

```bash
pip install 'onestep[redis]'
```

## 快速开始

### 启动 Redis

使用 Docker 快速启动：

```bash
docker run -d --name redis \
  --restart=always \
  -p 6379:6379 \
  redis:7
```

### 基本使用

```python
from onestep import OneStepApp, RedisConnector

app = OneStepApp("redis-demo")

# 创建连接
redis = RedisConnector("redis://localhost:6379")

# 创建 Stream 作为 Source
source = redis.stream(
    "jobs",
    group="workers",
    batch_size=100,
    poll_interval_s=0.5,
)

# 创建 Stream 作为 Sink
sink = redis.stream("processed")


@app.task(source=source, emit=sink, concurrency=8)
async def process_job(ctx, item):
    print(f"处理任务: {item}")
    return {"job": item["job"], "status": "done"}


if __name__ == "__main__":
    app.run()
```

## Stream 配置

### 基本参数

```python
source = redis.stream(
    stream="my_stream",     # Stream 名称
    group="my_group",       # 消费者组名称
    consumer=None,          # 消费者名称（默认自动生成）
    batch_size=100,         # 每次拉取的消息数量
    poll_interval_s=0.5,    # 轮询间隔（秒）
)
```

### 消费者组

消费者组允许多个消费者共享同一个 Stream：

```python
# 消费者组会自动创建（如果不存在）
source = redis.stream(
    "jobs",
    group="workers",
)

# 多个进程使用相同的 group 名称
# Redis 会自动分配消息给不同的消费者
```

### Stream 修剪

控制 Stream 大小：

```python
source = redis.stream(
    "jobs",
    group="workers",
    maxlen=10000,  # 保留最新 10000 条消息
)
```

## 发布消息

### 通过 Sink 发布

任务返回值自动发布：

```python
@app.task(source=..., emit=sink)
async def process(ctx, item):
    return {"result": "data"}  # 自动发布到 sink
```

### 手动发布

```python
import asyncio

async def main():
    sink = redis.stream("my_stream")
    
    # 发布单条
    await sink.publish({"job": "data"})
    
    # 发布多条
    for i in range(100):
        await sink.publish({"id": i})

asyncio.run(main())
```

## 确认机制

Redis Streams 消息在任务成功完成后自动确认（XACK）：

- **成功**: 自动 XACK
- **重试**: 消息留在 PEL（Pending Entries List）中
- **失败**: 消息留在 PEL 中，可通过 XCLAIM 重新处理

### Pending 消息处理

```python
source = redis.stream(
    "jobs",
    group="workers",
    claim_interval_s=60,      # 检查 pending 消息的间隔
    claim_min_idle_s=300,     # 消息空闲多久后可以被认领
)
```

未确认的消息会在下次轮询时被重新认领（XCLAIM）。

## 多消费者

同一 Stream 可以启动多个消费者，实现负载均衡：

```python
# 在多台机器上运行相同代码
# Redis Streams 会自动分配消息
@app.task(source=source, concurrency=4)
async def process(ctx, item):
    ...
```

## YAML 配置

```yaml
connectors:
  redis:
    type: redis
    url: "redis://localhost:6379"
  
  jobs:
    type: redis_stream
    connector: redis
    stream: "jobs"
    group: "workers"
    batch_size: 100
  
  results:
    type: redis_stream
    connector: redis
    stream: "results"

tasks:
  - name: process_jobs
    source: jobs
    emit: results
    concurrency: 8
```

## 与 RabbitMQ 对比

| 特性 | Redis Streams | RabbitMQ |
|------|--------------|----------|
| 消息持久化 | ✅ AOF/RDB | ✅ 持久化队列 |
| 消费者组 | ✅ 原生支持 | ✅ 需配置 |
| 消息确认 | ✅ XACK | ✅ Ack/Nack |
| 死信队列 | ❌ 需手动实现 | ✅ 原生支持 |
| 延迟消息 | ❌ 需额外实现 | ✅ 延迟插件 |
| 吞吐量 | 极高 | 高 |
| 部署复杂度 | 低 | 中 |
| 管理界面 | Redis CLI / Insight | Web UI |

**选择建议**:
- **Redis Streams**: 轻量级、高吞吐、已有 Redis 环境
- **RabbitMQ**: 需要复杂路由、延迟消息、死信队列

## 最佳实践

### 1. 批量大小

根据任务处理时间和内存调整：

```python
# 高吞吐场景：较大 batch_size
source = redis.stream("high_volume", group="workers", batch_size=500)

# 低延迟场景：较小 batch_size
source = redis.stream("low_latency", group="workers", batch_size=10)
```

### 2. 轮询间隔

```python
# 高吞吐：较短间隔
source = redis.stream(..., poll_interval_s=0.1)

# 低负载：较长间隔（节省 CPU）
source = redis.stream(..., poll_interval_s=1.0)
```

### 3. Stream 命名

使用有意义的命名约定：

```python
# 按业务命名
redis.stream("orders:created", group="order-processor")
redis.stream("payments:pending", group="payment-worker")

# 按环境隔离
redis.stream(f"prod:jobs", group="workers")
redis.stream(f"dev:jobs", group="workers")
```

### 4. 监控 Stream 长度

```python
# 在应用启动时检查 Stream 状态
async def check_stream_health():
    info = await redis.client.xinfo_stream("jobs")
    length = info["length"]
    if length > 100000:
        print(f"警告: Stream 过长 ({length} 条消息)")
```

### 5. 优雅关闭

```python
from onestep import OneStepApp, RedisConnector

app = OneStepApp("redis-demo", shutdown_timeout_s=30.0)

# shutdown_timeout_s 控制关闭时等待 inflight 任务完成的时间
```
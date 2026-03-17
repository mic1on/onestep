---
title: AWS SQS | Broker
outline: deep
---

# AWS SQS

AWS SQS (Simple Queue Service) 是 AWS 提供的托管消息队列服务。

## 安装

```bash
pip install 'onestep[sqs]'
```

## 配置认证

### 环境变量（推荐）

```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1
```

### 或使用 IAM Role（EC2/Lambda）

在 EC2 或 Lambda 上运行时，自动使用 IAM Role 认证，无需配置密钥。

## 基本用法

```python
from onestep import OneStepApp, SQSConnector

app = OneStepApp("sqs-demo")

# 创建连接器
sqs = SQSConnector(region_name="us-east-1")

# 创建队列 Source
source = sqs.queue(
    "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    prefetch=10,
)

# 创建队列 Sink
sink = sqs.queue(
    "https://sqs.us-east-1.amazonaws.com/123456789012/results-queue",
)


@app.task(source=source, emit=sink, concurrency=8)
async def process_message(ctx, item):
    print(f"处理消息：{item}")
    return {"result": "done"}


if __name__ == "__main__":
    app.run()
```

## 队列配置

### 标准队列

```python
sqs = SQSConnector(region_name="us-east-1")
source = sqs.queue(
    "https://sqs.us-east-1.amazonaws.com/123456789012/standard-queue"
)
```

### FIFO 队列

```python
source = sqs.queue(
    "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue.fifo",
    message_group_id="default-group",  # FIFO 必需
)
```

### 高级配置

```python
source = sqs.queue(
    queue_url="https://sqs.../my-queue",
    prefetch=50,                    # 预取数量
    delete_batch_size=10,           # 批量删除数量
    delete_flush_interval_s=0.5,    # 批量删除间隔
    heartbeat_interval_s=15,        # 心跳间隔
    heartbeat_visibility_timeout=60, # 可见性超时
)
```

## 发布消息

### 通过 Sink 发布

```python
@app.task(source=..., emit=sink)
async def process(ctx, item):
    return {"result": "data"}  # 自动发布到 sink
```

### 手动发布

```python
import asyncio

async def main():
    sink = sqs.queue("https://sqs.../my-queue")
    
    # 发布单条
    await sink.publish({"job": "data"})
    
    # 发布多条
    for i in range(100):
        await sink.publish({"id": i})

asyncio.run(main())
```

### FIFO 消息分组

```python
sink = sqs.queue(
    "https://sqs.../my-queue.fifo",
    message_group_id="group-1",
)

# 按用户 ID 分组
async def publish_for_user(user_id, data):
    sink = sqs.queue(
        "https://sqs.../my-queue.fifo",
        message_group_id=f"user-{user_id}",
    )
    await sink.publish(data)
```

## 可见性超时

消息被消费后，在可见性超时内其他消费者不可见：

```python
source = sqs.queue(
    "https://sqs.../my-queue",
    heartbeat_interval_s=15,        # 每 15 秒续期
    heartbeat_visibility_timeout=60, # 续期到 60 秒
)


@app.task(source=source)
async def long_task(ctx, item):
    await asyncio.sleep(45)  # 长任务，自动续期可见性
```

## 死信队列

配置 SQS 死信队列：

```python
# 在 AWS 控制台或 CloudFormation 中配置死信队列
# 主队列的 Redrive Policy 指向死信队列

# onestep 中处理死信
dead_letter = sqs.queue("https://sqs.../dead-letter-queue")


@app.task(source=dead_letter)
async def handle_dead_letter(ctx, item):
    print(f"死信消息：{item}")
```

## YAML 配置

```yaml
connectors:
  sqs:
    type: sqs
    region_name: "us-east-1"
  
  jobs:
    type: sqs_queue
    connector: sqs
    queue_url: "https://sqs.us-east-1.amazonaws.com/123456789012/jobs"
  
  results:
    type: sqs_queue
    connector: sqs
    queue_url: "https://sqs.us-east-1.amazonaws.com/123456789012/results"

tasks:
  - name: process_jobs
    source: jobs
    emit: results
    concurrency: 8
```

## 最佳实践

### 1. 使用 IAM Role

在 EC2/Lambda 上使用 IAM Role，避免硬编码密钥：

```python
# 自动使用实例的 IAM Role
sqs = SQSConnector(region_name="us-east-1")
```

### 2. 批量操作

```python
# 调整批量参数提高吞吐
source = sqs.queue(
    "https://sqs.../my-queue",
    prefetch=50,
    delete_batch_size=10,
    delete_flush_interval_s=0.5,
)
```

### 3. 并发控制

```python
# 根据任务处理时间调整并发
@app.task(source=source, concurrency=16)
async def fast_task(ctx, item):
    ...

@app.task(source=source, concurrency=4)
async def slow_task(ctx, item):
    ...
```

### 4. 错误处理

```python
from onestep import MaxAttempts

@app.task(
    source=source,
    retry=MaxAttempts(max_attempts=3, delay_s=5.0)
)
async def might_fail(ctx, item):
    ...
```

### 5. 监控

使用 CloudWatch 监控队列：

- `ApproximateNumberOfMessagesVisible`: 可见消息数
- `ApproximateNumberOfMessagesNotVisible`: 不可见消息数
- `NumberOfMessagesSent`: 发送消息数
- `NumberOfMessagesReceived`: 接收消息数
- `NumberOfMessagesDeleted`: 删除消息数
- `NumberOfMessagesFailed`: 失败消息数
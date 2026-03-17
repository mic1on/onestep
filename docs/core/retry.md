---
title: Retry | 核心
outline: deep
---

# Retry

重试组件可以在任务执行失败时自动重试。

## 内置重试策略

### NoRetry (默认)

不重试，任务失败后直接终止。

### MaxAttempts

最多重试指定次数：

```python
from onestep import MaxAttempts, IntervalSource, OneStepApp

app = OneStepApp("retry-demo")


@app.task(
    source=IntervalSource.every(seconds=10),
    retry=MaxAttempts(max_attempts=3, delay_s=1.0)
)
async def might_fail(ctx, _):
    import random
    if random.random() < 0.7:
        raise Exception("随机失败")
    print("成功!")
```

参数：
- `max_attempts`: 最大重试次数（包含首次执行）
- `delay_s`: 重试间隔秒数
- `exponential_backoff`: 是否使用指数退避
- `max_delay_s`: 最大延迟秒数（配合指数退避）

### 自定义重试策略

实现 `RetryPolicy` 接口：

```python
from onestep import RetryPolicy, FailureInfo

class MyRetryPolicy(RetryPolicy):
    def should_retry(self, failure: FailureInfo, attempts: int) -> bool:
        # 根据失败类型决定是否重试
        if failure.kind == "timeout":
            return attempts < 5
        if failure.kind == "error":
            # 业务异常不重试
            return False
        return False
    
    def retry_delay(self, failure: FailureInfo, attempts: int) -> float:
        # 返回重试延迟秒数
        return min(2 ** attempts, 60)


@app.task(source=..., retry=MyRetryPolicy())
async def my_task(ctx, item):
    ...
```

## 失败类型

`FailureInfo` 包含以下信息：

- `kind`: 失败类型
  - `error`: 业务异常
  - `timeout`: 执行超时
  - `cancelled`: 任务取消
- `exception_type`: 异常类型
- `message`: 异常消息

## 死信队列

配置 `dead_letter` 将终端失败的消息发送到死信队列：

```python
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("demo")
source = MemoryQueue("incoming")
dead_letter = MemoryQueue("dead-letter")


@app.task(
    source=source,
    dead_letter=dead_letter,
    retry=MaxAttempts(max_attempts=3)
)
async def process(ctx, item):
    if item.get("should_fail"):
        raise Exception("处理失败")
    return item


# 处理死信
@app.task(source=dead_letter)
async def handle_dead_letter(ctx, item):
    print(f"死信消息: {item}")
    # payload 在 item["payload"]
    # 失败信息在 item["failure"]
```

死信消息结构：

```python
{
    "payload": {...},  # 原始消息
    "failure": {
        "kind": "error",
        "exception_type": "Exception",
        "message": "处理失败"
    }
}
```

## 执行超时

使用 `timeout_s` 参数限制任务执行时间：

```python
@app.task(source=..., timeout_s=30.0)
async def long_running(ctx, item):
    # 超过 30 秒会被取消，触发 timeout 类型失败
    await asyncio.sleep(60)
```

超时后会触发 `timeout` 类型的失败，可根据 `FailureInfo.kind` 决定重试策略。
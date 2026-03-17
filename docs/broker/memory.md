---
title: Memory | Broker
outline: deep
---

# Memory

内存队列，用于开发、测试和进程内通信。

## 基本用法

```python
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("memory-demo")

# 创建队列
source = MemoryQueue("incoming")
sink = MemoryQueue("processed")


@app.task(source=source, emit=sink, concurrency=4)
async def process(ctx, item):
    print(f"处理: {item}")
    return {"processed": item}


async def main():
    # 发布消息
    await source.publish({"data": "test"})
    await source.publish({"data": "test2"})
    
    # 启动处理
    await app.serve()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## 发布消息

### 单条发布

```python
queue = MemoryQueue("my_queue")
await queue.publish({"key": "value"})
```

### 批量发布

```python
for i in range(100):
    await queue.publish({"id": i})
```

## 队列操作

### 查看队列长度

```python
size = source.qsize()
print(f"队列中有 {size} 条消息")
```

### 清空队列

```python
while not source.empty():
    await source.get()
```

## 使用场景

### 1. 开发测试

```python
# 测试任务逻辑，无需外部依赖
@app.task(source=MemoryQueue("test"), concurrency=1)
async def test_task(ctx, item):
    ...
```

### 2. 进程内管道

```python
# 任务链：stage1 -> stage2 -> stage3
stage1_out = MemoryQueue("stage1-out")
stage2_out = MemoryQueue("stage2-out")


@app.task(source=MemoryQueue("input"), emit=stage1_out)
async def stage1(ctx, item):
    return item * 2


@app.task(source=stage1_out, emit=stage2_out)
async def stage2(ctx, item):
    return item + 1


@app.task(source=stage2_out)
async def stage3(ctx, item):
    print(f"最终结果：{item}")
```

### 3. 死信队列

```python
from onestep import MaxAttempts, MemoryQueue

dead_letter = MemoryQueue("dead-letter")


@app.task(
    source=MemoryQueue("main"),
    dead_letter=dead_letter,
    retry=MaxAttempts(max_attempts=3)
)
async def risky_task(ctx, item):
    if item.get("fail"):
        raise Exception("失败")
    return item


# 处理死信
@app.task(source=dead_letter)
async def handle_dead_letter(ctx, item):
    print(f"死信: {item}")
```

## YAML 配置

```yaml
connectors:
  input:
    type: memory
  
  output:
    type: memory

tasks:
  - name: process
    source: input
    emit: output
```

## 注意事项

- 内存队列仅在进程内有效，进程重启后数据丢失
- 不适合分布式场景（使用 RabbitMQ/SQS 等）
- 适合开发、测试、简单管道
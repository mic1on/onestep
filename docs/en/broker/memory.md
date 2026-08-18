---
title: Memory | Broker
outline: deep
---

# Memory

In-memory queue for development, testing, and in-process communication.

## Basic Usage

```python
from onestep import MemoryQueue, OneStepApp

app = OneStepApp("memory-demo")

# Create a queue
source = MemoryQueue("incoming")
sink = MemoryQueue("processed")


@app.task(source=source, emit=sink, concurrency=4)
async def process(ctx, item):
    print(f"Processing: {item}")
    return {"processed": item}


async def main():
    # Publish messages
    await source.publish({"data": "test"})
    await source.publish({"data": "test2"})
    
    # Start processing
    await app.serve()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

## Publishing Messages

### Single Publish

```python
queue = MemoryQueue("my_queue")
await queue.publish({"key": "value"})
```

### Batch Publish

```python
for i in range(100):
    await queue.publish({"id": i})
```

## Queue Operations

### Checking Queue Length

```python
size = source.size()
print(f"Queue has {size} messages")
```

## Use Cases

### 1. Development & Testing

```python
# Test task logic without external dependencies
@app.task(source=MemoryQueue("test"), concurrency=1)
async def test_task(ctx, item):
    ...
```

### 2. In-Process Pipeline

```python
# Task chain: stage1 -> stage2 -> stage3
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
    print(f"Final result: {item}")
```

### 3. Dead Letter Queue

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
        raise Exception("Failed")
    return item


# Process dead letters
@app.task(source=dead_letter)
async def handle_dead_letter(ctx, item):
    print(f"Dead letter: {item}")
```

## YAML Configuration

```yaml
resources:
  input:
    type: memory
  
  output:
    type: memory

tasks:
  - name: process
    source: input
    emit: output
```

## Notes

- In-memory queues are only valid within the process; data is lost on restart
- Not suitable for distributed scenarios (use RabbitMQ/SQS instead)
- Suitable for development, testing, and simple pipelines

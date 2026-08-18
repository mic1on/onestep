---
title: Retry | Core
outline: deep
---

# Retry

The retry component can automatically retry when a task fails.

## Built-in Retry Strategies

### NoRetry (default)

No retry, terminates immediately on failure.

### MaxAttempts

Retry up to a specified number of times:

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
        raise Exception("Random failure")
    print("Success!")
```

Parameters:
- `max_attempts`: Maximum number of attempts (including the first execution)
- `delay_s`: Retry interval in seconds

### Custom Retry Strategy

Implement the `RetryPolicy` interface:

```python
from onestep import RetryAction, RetryDecision


class MyRetryPolicy:
    def on_error(self, envelope, exc, failure):
        if failure.kind == "timeout":
            next_attempt = envelope.attempts + 1
            if next_attempt < 5:
                return RetryAction(RetryDecision.RETRY, delay_s=2.0)
        if failure.kind == "error":
            return RetryAction(RetryDecision.FAIL)
        return RetryAction(RetryDecision.FAIL)


@app.task(source=..., retry=MyRetryPolicy())
async def my_task(ctx, item):
    ...
```

## Failure Types

`FailureInfo` contains the following information:

- `kind`: Failure type
  - `error`: Business exception
  - `timeout`: Execution timeout
  - `cancelled`: Task cancelled
- `exception_type`: Exception type
- `message`: Exception message

## Dead Letter Queue

Configure `dead_letter` to send terminally failed messages to a dead letter queue:

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
        raise Exception("Processing failed")
    return item


# Process dead letters
@app.task(source=dead_letter)
async def handle_dead_letter(ctx, item):
    print(f"Dead letter message: {item}")
    # Payload in item["payload"]
    # Failure info in item["failure"]
```

Dead letter message structure:

```python
{
    "payload": {...},  # Original message
    "failure": {
        "kind": "error",
        "exception_type": "Exception",
        "message": "Processing failed"
    }
}
```

## Execution Timeout

Use the `timeout_s` parameter to limit task execution time:

```python
@app.task(source=..., timeout_s=30.0)
async def long_running(ctx, item):
    # Exceeding 30 seconds will cancel the task, triggering a timeout failure
    await asyncio.sleep(60)
```

On timeout, a `timeout` type failure is triggered, and the retry strategy can make decisions based on `FailureInfo.kind`.

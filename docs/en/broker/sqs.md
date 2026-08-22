---
title: AWS SQS | Broker
outline: deep
---

# AWS SQS

AWS SQS (Simple Queue Service) is AWS's managed message queue service.

## Installation

```bash
pip install onestep-sqs
```

## Authentication Configuration

### Environment Variables (Recommended)

```bash
export AWS_ACCESS_KEY_ID=your_access_key
export AWS_SECRET_ACCESS_KEY=your_secret_key
export AWS_DEFAULT_REGION=us-east-1
```

### Or Use IAM Role (EC2/Lambda)

When running on EC2 or Lambda, IAM Role authentication is used automatically - no need to configure keys.

## Basic Usage

```python
from onestep import OneStepApp
from onestep_sqs import SQSConnector

app = OneStepApp("sqs-demo")

# Create connector
sqs = SQSConnector(region_name="us-east-1")

# Create queue Source
source = sqs.queue(
    "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue",
    batch_size=10,
)

# Create queue Sink
sink = sqs.queue(
    "https://sqs.us-east-1.amazonaws.com/123456789012/results-queue",
)


@app.task(source=source, emit=sink, concurrency=8)
async def process_message(ctx, item):
    print(f"Processing message: {item}")
    return {"result": "done"}


if __name__ == "__main__":
    app.run()
```

## Queue Configuration

### Standard Queue

```python
sqs = SQSConnector(region_name="us-east-1")
source = sqs.queue(
    "https://sqs.us-east-1.amazonaws.com/123456789012/standard-queue"
)
```

### FIFO Queue

```python
source = sqs.queue(
    "https://sqs.us-east-1.amazonaws.com/123456789012/my-queue.fifo",
    message_group_id="default-group",  # Required for FIFO
)
```

### Advanced Configuration

```python
source = sqs.queue(
    "https://sqs.../my-queue",
    batch_size=10,                  # Messages per poll
    wait_time_s=20,                 # Long poll seconds
    delete_batch_size=10,           # Batch delete count
    delete_flush_interval_s=0.5,    # Batch delete interval
    heartbeat_interval_s=15,        # Heartbeat interval
    heartbeat_visibility_timeout=60, # Visibility timeout
)
```

## Publishing Messages

### Publish via Sink

```python
@app.task(source=..., emit=sink)
async def process(ctx, item):
    return {"result": "data"}  # Auto-published to sink
```

### Manual Publish

```python
import asyncio

async def main():
    sink = sqs.queue("https://sqs.../my-queue")
    
    # Publish single
    await sink.publish({"job": "data"})
    
    # Publish multiple
    for i in range(100):
        await sink.publish({"id": i})

asyncio.run(main())
```

### FIFO Message Grouping

```python
sink = sqs.queue(
    "https://sqs.../my-queue.fifo",
    message_group_id="group-1",
)

# Group by user ID
async def publish_for_user(user_id, data):
    sink = sqs.queue(
        "https://sqs.../my-queue.fifo",
        message_group_id=f"user-{user_id}",
    )
    await sink.publish(data)
```

## Visibility Timeout

After a message is consumed, it becomes invisible to other consumers until the visibility timeout expires:

```python
source = sqs.queue(
    "https://sqs.../my-queue",
    heartbeat_interval_s=15,        # Renew every 15 seconds
    heartbeat_visibility_timeout=60, # Renew to 60 seconds
)


@app.task(source=source)
async def long_task(ctx, item):
    await asyncio.sleep(45)  # Long task, visibility auto-renewed
```

## Dead Letter Queue

Configure SQS dead letter queue:

```python
# Configure the dead letter queue in AWS Console or CloudFormation
# The main queue's Redrive Policy points to the dead letter queue

# Handle dead letters in onestep
dead_letter = sqs.queue("https://sqs.../dead-letter-queue")


@app.task(source=dead_letter)
async def handle_dead_letter(ctx, item):
    print(f"Dead letter message: {item}")
```

## YAML Configuration

```yaml
resources:
  sqs:
    type: sqs
    region_name: "us-east-1"
  
  jobs:
    type: sqs_queue
    connector: sqs
    url: "https://sqs.us-east-1.amazonaws.com/123456789012/jobs"
  
  results:
    type: sqs_queue
    connector: sqs
    url: "https://sqs.us-east-1.amazonaws.com/123456789012/results"

tasks:
  - name: process_jobs
    source: jobs
    emit: results
    concurrency: 8
```

## Best Practices

### 1. Use IAM Role

Use IAM Role on EC2/Lambda to avoid hard-coding keys:

```python
# Automatically uses the instance's IAM Role
sqs = SQSConnector(region_name="us-east-1")
```

### 2. Batch Operations

```python
# Adjust batch parameters for higher throughput
source = sqs.queue(
    "https://sqs.../my-queue",
    batch_size=10,
    delete_batch_size=10,
    delete_flush_interval_s=0.5,
)
```

### 3. Concurrency Control

```python
# Adjust concurrency based on task processing time
@app.task(source=source, concurrency=16)
async def fast_task(ctx, item):
    ...

@app.task(source=source, concurrency=4)
async def slow_task(ctx, item):
    ...
```

### 4. Error Handling

```python
from onestep import MaxAttempts

@app.task(
    source=source,
    retry=MaxAttempts(max_attempts=3, delay_s=5.0)
)
async def might_fail(ctx, item):
    ...
```

### 5. Monitoring

Use CloudWatch to monitor queues:

- `ApproximateNumberOfMessagesVisible`: Visible messages count
- `ApproximateNumberOfMessagesNotVisible`: Invisible messages count
- `NumberOfMessagesSent`: Sent messages count
- `NumberOfMessagesReceived`: Received messages count
- `NumberOfMessagesDeleted`: Deleted messages count
- `NumberOfMessagesFailed`: Failed messages count


## SNS Topic Sink {#sns-topic-sink}

SNS is a publish/subscribe service: it can only publish, not consume, so `SNSTopic` implements `Sink` only.
To consume SNS messages, the standard approach is to subscribe an SQS queue to the topic and use `sqs_queue` as the Source.

```python
from onestep import MemoryQueue, OneStepApp
from onestep_sqs import SNSConnector

app = OneStepApp("sns-demo")
sns = SNSConnector(region_name="us-east-1")
notify = sns.topic(
    "arn:aws:sns:us-east-1:123456789012:events",
    subject="onestep-event",
)

@app.task(source=MemoryQueue("jobs"), emit=notify)
async def publish_event(ctx, job):
    return {"id": job["id"], "status": "done"}
```

The task return value is envelope-encoded and published as the SNS `Message`. Configuration options:

- `subject`: optional SNS `Subject`.
- `message_attributes`: raw SNS `MessageAttributes` mapping used by subscription filter policies.
- `message_group_id` / `deduplication_id_factory`: required group id for FIFO topics (ARN ending in `.fifo`); the deduplication factory is optional and receives an `Envelope`, returning a deduplication id string.
- `retry_delay_s`: retry backoff hint applied when normalizing connector errors.

YAML configuration:

```yaml
resources:
  sns:
    type: sns
    region_name: us-east-1
  notify:
    type: sns_topic
    connector: sns
    arn: arn:aws:sns:us-east-1:123456789012:events
    subject: onestep-event
```

---
title: 'Case: SQS Messages Reliably Persisted to MySQL | Guide'
outline: deep
---

# Case: SQS Messages Reliably Persisted to MySQL

This case reliably writes business events from an AWS SQS queue into a MySQL table.
It suits scenarios where an upstream pushes events to SQS and a downstream must
persist them by business key: SQS delivers at least once, and messages are
re-delivered after a crash or visibility timeout, so persistence must be idempotent.

```text
sqs_queue: events
  └─ handler:to_event_row
       └─ mysql_table_sink: upsert / event_id
```

## Goals & Boundaries

- SQS is at-least-once delivery: the same message may be received more than once.
  The Delivery is only `ack()`ed after the handler succeeds and the Sink write
  completes; only then is the message deleted from SQS.
- Persistence uses `mode: upsert` with the business key `event_id`, so redelivery
  hits an existing row and updates it instead of creating a duplicate.
- A handler exception triggers task retries; when retries are exhausted the message
  is handled per the `on_fail` policy (`leave` keeps it for SQS Redrive Policy to
  move to a dead-letter queue; `release` makes it immediately visible again).
- Long-running tasks must extend the visibility timeout via heartbeat, otherwise a
  message becomes visible mid-processing and is picked up again by another worker.

## Prerequisites

Install these versions or later:

```bash
pip install onestep-sqs 'onestep-sql[mysql]>=0.1.0'
```

Before starting, verify:

1. The SQS queue exists, with a Redrive Policy pointing to a dead-letter queue
   configured in the AWS console (recommended).
2. The target MySQL table has a unique key or unique index covering `event_id`,
   otherwise `upsert` cannot detect conflicts.
3. The runtime identity gets SQS permission via environment variables or an
   EC2/Lambda IAM Role. Do not hard-code credentials.
4. Handler output field names match the target table columns; list/dict values are
   serialized per the `serialize_json` rule.

## Full YAML

Save as `worker.yaml`. Credentials are provided only through environment variables
or an IAM Role; never write them in plaintext YAML.

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: sqs-to-mysql
  shutdown_timeout_s: 60
  strict_env: true
  logging:
    level: "${SQS_MYSQL_LOG_LEVEL:-INFO}"

resources:
  sqs:
    type: sqs
    region_name: "${AWS_DEFAULT_REGION:-us-east-1}"

  events:
    type: sqs_queue
    connector: sqs
    url: "${SQS_EVENTS_URL}"
    batch_size: 10
    wait_time_s: 20
    # Extend visibility via heartbeat when processing takes longer.
    heartbeat_interval_s: 15
    heartbeat_visibility_timeout: 60
    # After retries are exhausted, keep the message for SQS Redrive Policy.
    on_fail: leave

  mysql_main:
    type: mysql
    dsn: "${SQS_MYSQL_DSN}"

  events_sink:
    type: mysql_table_sink
    connector: mysql_main
    table: events
    mode: upsert
    keys: [event_id]
    # Only rewrite these columns; unlisted columns (e.g. created_at) are preserved.
    update_columns: [payload, status]
    update_expr:
      updated_at: "NOW(6)"
    serialize_json: auto

tasks:
  - name: ingest_events
    description: Ingest SQS events into MySQL with idempotent upsert
    source: events
    emit: events_sink
    concurrency: 16
    timeout_s: 45
    retry:
      type: exponential_backoff
      max_attempts: 5
      min_delay_s: 1
      max_delay_s: 30
      jitter: full
    handler: handler:to_event_row
```

`events.batch_size: 10` is the max messages per `ReceiveMessage` (the SQS limit is
10); `concurrency: 16` caps in-flight deliveries. The heartbeat extends visibility
to 60 seconds, and `heartbeat_interval_s: 15` satisfies the requirement that the
refresh interval be well below the visibility timeout.

## Handler Contract

YAML only wires resources; field transformation is done in the Python handler.
`handler:to_event_row` receives `(ctx, item)`, where `item` is the decoded SQS
message body, and returns a dict of columns for the `events` table.

The minimum contract is to return a stable `event_id`:

```python
async def to_event_row(ctx, item):
    # event_id must map stably to the business event for upsert dedup to work.
    return {
        "event_id": item["id"],
        "payload": item,        # dict, serialized to JSON via serialize_json=auto
        "status": item.get("status", "received"),
    }
```

If a message can never be processed (dirty data), let the handler raise explicitly
so that after retries it goes to the dead-letter queue, rather than silently
`ack()`ing and dropping it.

## Operation & Recovery

### At-least-once and idempotency

SQS does not guarantee exactly-once delivery. Any logic that assumes a message
arrives only once is wrong. Reliable persistence relies on two things together:

1. The Delivery is only `ack()`ed (deleting the SQS message) after the Sink write
   succeeds; messages that crash before ack are redelivered.
2. `upsert` + unique key makes redelivery hit an existing row and only update it.

### Visibility timeout and heartbeat

A picked message enters a visibility timeout window during which other consumers
cannot see it. When processing may exceed the default timeout, you must enable
heartbeat extension, otherwise the message becomes visible mid-processing and gets
picked up again. Rule of thumb:

```text
heartbeat_interval_s is clearly smaller than heartbeat_visibility_timeout
```

### Dead-letter and failure policy

- `on_fail: leave` (this case): after retries are exhausted, keep the message so
  SQS Redrive Policy moves it to the dead-letter queue once max receive count is
  reached. Recommended when dirty data needs manual investigation.
- `on_fail: release`: after retries are exhausted, make the message visible
  immediately so it keeps being redelivered; use only when you are sure failures
  are transient and no dead-letter queue is configured, otherwise it causes a
  delivery storm.

### Observability

With INFO logging enabled, watch SQS CloudWatch metrics and task events:

| Metric/Event | Purpose |
|---|---|
| `ApproximateNumberOfMessagesVisible` | Backlog depth; tells whether consumption keeps up. |
| `ApproximateNumberOfMessagesNotVisible` | Messages in processing (incl. visibility refresh). |
| Dead-letter queue message count | Records that failed persistence and need investigation. |
| Task failure and retry events | Locate handler or MySQL-side errors. |

Do not log DSNs, AWS credentials, or sensitive fields from messages.

## Trade-offs

| Parameter | Value here | Trade-off |
|---|---:|---|
| SQS `batch_size` | 10 | Max receive per call; fewer API calls. |
| `wait_time_s` | 20 | Long polling lowers empty-poll cost; SQS max is 20s. |
| `concurrency` | 16 | Caps in-flight deliveries; tune to MySQL write capacity. |
| `heartbeat_visibility_timeout` | 60 | Covers the longest single-message processing with margin. |
| `max_attempts` | 5 | Transient failures retry; exhaustion routes to dead-letter. |
| `on_fail` | `leave` | Works with SQS Redrive Policy for dead-lettering. |

## Related

- [AWS SQS Connector](/en/broker/sqs)
- [MySQL: Table Sink and Conflict Policy](/en/broker/mysql#table-sink)
- [Retry & Dead Letter](/en/core/retry)
- [YAML Task Definition](/en/yaml-task-definition)

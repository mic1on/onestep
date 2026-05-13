# Connectors

Use this reference when wiring onestep resources to queues, polling backends, schedule sources, webhook sources, or HTTP sinks.

## General Rules

- Install only the needed extra: `onestep[mysql]`, `onestep[rabbitmq]`, `onestep[redis]`, `onestep[sqs]`, or `onestep[yaml]`.
- Prefer environment variables for DSNs, tokens, and queue URLs in YAML.
- In YAML, define shared connection resources first, then sources/sinks that reference them by name.
- Keep connector options minimal until the deployment requires tuning.

## Memory

Python:

```python
from onestep import MemoryQueue

incoming = MemoryQueue("incoming")
processed = MemoryQueue("processed")
```

YAML:

```yaml
resources:
  incoming:
    type: memory
    name: incoming
```

Memory queues are useful for examples and tests, not durable production queues.

## Interval And Cron

```yaml
resources:
  tick:
    type: interval
    minutes: 5
    immediate: true
    overlap: skip

  nightly:
    type: cron
    expression: "0 2 * * *"
    timezone: Asia/Shanghai
    overlap: skip
```

Use `overlap: skip` for scheduled jobs that should not run concurrently.

## MySQL

```yaml
resources:
  mysql_main:
    type: mysql
    dsn: "${MYSQL_DSN}"

  users_source:
    type: mysql_incremental
    connector: mysql_main
    table: users
    key: id
    cursor: [updated_at, id]

  users_sink:
    type: mysql_table_sink
    connector: mysql_main
    table: dw_users
    mode: upsert
    keys: [id]
```

Useful types:

- `mysql_table_queue`: table-backed queue with claim/ack/nack fields.
- `mysql_incremental`: incremental polling with cursor state.
- `mysql_table_sink`: insert/upsert output table.
- `mysql_state_store` / `mysql_cursor_store`: durable app or cursor state.

Bind app-level state explicitly when needed:

```yaml
app:
  name: billing-sync
  state: app_state

resources:
  app_state:
    type: mysql_state_store
    connector: mysql_main
    table: onestep_state
```

## RabbitMQ

```yaml
resources:
  rmq:
    type: rabbitmq
    url: "${RABBITMQ_URL}"

  incoming:
    type: rabbitmq_queue
    connector: rmq
    queue: billing.incoming
    durable: true
    prefetch: 10
```

Add exchange, binding, and publisher options only when the topology requires them.

## Redis Streams

```yaml
resources:
  redis_main:
    type: redis
    url: "${REDIS_URL}"

  incoming:
    type: redis_stream
    connector: redis_main
    stream: billing.incoming
    group: billing-workers
    consumer: "${HOSTNAME:-local}"
    create_group: true
```

Use stable consumer names in production when replay and pending-entry behavior matters.

## SQS

```yaml
resources:
  aws:
    type: sqs
    region_name: "${AWS_REGION:-us-east-1}"

  incoming:
    type: sqs_queue
    connector: aws
    url: "${SQS_QUEUE_URL}"
    wait_time_s: 20
    visibility_timeout: 120
```

For FIFO queues, configure message group and deduplication behavior deliberately.

## Webhook

```yaml
resources:
  webhook:
    type: webhook
    path: /hooks/billing
    methods: [POST]
    host: 0.0.0.0
    port: 8080
    auth:
      type: bearer
      token: "${WEBHOOK_TOKEN}"
```

Use webhooks when inbound HTTP should become task deliveries. Keep parsing and business validation in Python unless the built-in parser option is enough.

## HTTP Sink

Python:

```python
import os

from onestep import HttpSink

notify = HttpSink(
    "notify",
    url="https://example.com/hooks/events",
    headers={"Authorization": f"Bearer {os.environ['NOTIFY_TOKEN']}"},
    timeout_s=5.0,
    success_statuses=[200, 202],
)
```

For `GET` or `DELETE`, `HttpSink` does not send a request body. Static `params`
and mapping payload fields are encoded into the query string instead:

```python
lookup = HttpSink(
    "lookup",
    url="https://example.com/users",
    method="GET",
    params={"api_key": os.environ["API_KEY"]},
    success_statuses=[200],
)
```

YAML:

```yaml
resources:
  notify:
    type: http_sink
    url: "https://example.com/hooks/events"
    method: POST
    headers:
      Authorization: "Bearer ${NOTIFY_TOKEN}"
    params:
      source: onestep
    timeout_s: 5
    success_statuses: [200, 202]
```

`http_sink` sends task results as JSON for body methods such as `POST`, `PUT`,
and `PATCH`. It is a sink only, not a source. Use `WebhookSource` for inbound
HTTP and `HttpSink` for outbound HTTP.

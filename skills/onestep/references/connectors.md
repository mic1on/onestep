# Connectors

Use this reference when wiring onestep resources to queues, polling backends, schedule sources, webhook sources, or HTTP sinks.

## General Rules

- Install only the needed package: `onestep-mysql`, `onestep-postgres`,
  `onestep-mq`, `onestep-redis`, `onestep-sqs`, `onestep-kafka`,
  `onestep-elasticsearch`, `onestep-clickhouse`, `onestep-mongodb`, or
  `onestep[yaml]`.
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
    max_queued_runs: 1000

  nightly:
    type: cron
    expression: "0 2 * * *"
    timezone: Asia/Shanghai
    overlap: skip
    max_queued_runs: 1000
```

Use `overlap: skip` for scheduled jobs that should not run concurrently.
Use `max_queued_runs` to bound `overlap: queue` backlogs.

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
- `mysql_binlog`: row-based change stream with binlog file/position cursor state.
- `mysql_table_sink`: insert/upsert output table.
- `mysql_state_store` / `mysql_cursor_store`: durable app or cursor state.

Use `mysql_binlog` when downstream systems need insert/update/delete events:

```yaml
resources:
  mysql_cursor:
    type: mysql_cursor_store
    connector: mysql_main

  order_changes:
    type: mysql_binlog
    connector: mysql_main
    server_id: 18492
    schemas: [onestep]
    tables: [orders]
    events: [insert, update, delete]
    state: mysql_cursor
    state_key: orders-cdc
```

The source expects MySQL binary logging to be enabled with row logging,
for example `--log-bin`, `--server-id`, `--binlog-format=ROW`, and
`--binlog-row-image=FULL`.

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

## PostgreSQL execution source

The Python API process uses `ExecutionClient` and `pg.execution_backend()`.
YAML is the worker wiring layer and registers a source that claims only the
configured task names:

```yaml
resources:
  pg:
    type: postgres
    dsn: "${POSTGRES_DSN}"

  agent_jobs:
    type: postgres_execution_source
    connector: pg
    namespace: agent-api
    task_names: [run_agent]
    table: onestep_executions
    attempts_table: onestep_execution_attempts
    batch_size: 4
    poll_interval_s: 0.5
    lease_duration_s: 90
    heartbeat_interval_s: 30
    worker_id: "${HOSTNAME:-agent-worker}"
    auto_create: false

tasks:
  - name: run_agent
    source: agent_jobs
    handler:
      ref: agent_worker.tasks:run_agent
```

The invariant is `0 < heartbeat_interval_s <= lease_duration_s / 3`.
PostgreSQL execution stores eight observable states, attempt history, leases and
results. Payload/result inline values default to 1 MiB and metadata to 64 KiB.
It provides at-least-once execution and cooperative cancellation; use stable
idempotency keys for API retries and idempotent downstream writes.

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

## Kafka

```yaml
resources:
  kafka_main:
    type: kafka
    bootstrap_servers: "${KAFKA_BOOTSTRAP_SERVERS}"

  orders:
    type: kafka_topic
    connector: kafka_main
    topic: orders.events
    group_id: onestep-orders
    batch_size: 100
    poll_timeout_ms: 1000
```

Kafka uses consumer groups with auto commit disabled. Offsets are committed
only after processing reaches `ack()` or terminal `fail()`. Treat it as
at-least-once: output sinks may receive duplicates if the worker exits after
output send and before the Kafka commit succeeds.

`group_id` is required when a `kafka_topic` is used as a source. Sink-only
topics may omit it.

## Elasticsearch And OpenSearch

`onestep-elasticsearch` uses a shared HTTP(S) boundary for Elasticsearch and
OpenSearch. It provides an acknowledged bulk sink, not a search source:

```yaml
resources:
  search:
    type: elasticsearch
    hosts: ["${SEARCH_URL}"]
    distribution: auto
    username: "${SEARCH_USERNAME}"
    password: "${SEARCH_PASSWORD}"
    verify_certs: true
    ca_certs: "${SEARCH_CA_FILE:-/etc/ssl/certs/ca-certificates.crt}"
    request_timeout_s: 30

  events:
    type: elasticsearch_bulk_sink
    connector: search
    index: events-v1
    operation: index
    id_field: event_id
    chunk_size: 500
    max_chunk_bytes: 5000000
    refresh: false
```

Use `distribution: auto`, `elasticsearch`, or `opensearch`. Configure no auth
mode or exactly one of Basic, API key, or Bearer. A deterministic `id_field`
makes repeated `index` writes converge; auto-generated IDs and `create` writes
do not have that guarantee.

## ClickHouse

`onestep-clickhouse` inserts acknowledged row batches into an existing table:

```yaml
resources:
  analytics:
    type: clickhouse
    dsn: "${CLICKHOUSE_DSN}"
    client_options:
      connect_timeout: 10
      send_receive_timeout: 30

  events:
    type: clickhouse_table_sink
    connector: analytics
    table: events
    columns: [event_id, occurred_at, kind, payload]
    batch_size: 1000
    settings:
      async_insert: 0
```

Configured `columns` must exist in every row with no extra keys. Without
`columns`, the first row fixes insertion order and every later row must have the
same key set. If `async_insert` is enabled, also set `wait_for_async_insert: 1`;
fire-and-forget inserts are rejected.

## MongoDB

MongoDB change streams require a replica set or sharded cluster. Production
sources should bind an explicit durable cursor store:

```yaml
resources:
  mongo:
    type: mongodb
    uri: "${MONGODB_URI}"
    database: app
    client_options:
      serverSelectionTimeoutMS: 10000

  cursor_db:
    type: postgres
    dsn: "${POSTGRES_DSN}"

  events_cursor:
    type: postgres_cursor_store
    connector: cursor_db
    table: onestep_cursor

  events_poll:
    type: mongodb_polling
    connector: mongo
    collection: events
    cursor: [updated_at, _id]
    batch_size: 100
    poll_interval_s: 1
    state: events_cursor
    state_key: events-poll

  events_changes:
    type: mongodb_change_stream
    connector: mongo
    collection: events
    full_document: updateLookup
    max_await_time_ms: 1000
    batch_size: 100
    poll_interval_s: 0.1
    state: events_cursor
    state_key: events-change-stream

  archive:
    type: mongodb_collection_sink
    connector: mongo
    collection: events_archive
    mode: upsert
    keys: [event_id]
    ordered: true
    batch_size: 1000
```

Polling uses deterministic ascending keyset traversal with `_id` as the final
tie-breaker. It is not CDC: deletes are invisible and non-monotonic cursor
updates can be missed. Use raw change-stream events when those changes matter.

All three bulk sinks accept one mapping or a non-empty sequence of mappings and
await every backend chunk acknowledgement. A retry can repeat committed chunks;
use stable IDs/keys or backend dedup-aware schema design when duplicates matter.

MongoDB polling/change streams may use in-memory state for development. Production
restart guarantees require an explicit durable `state` cursor store. Change streams
emit raw events and default to `full_document: updateLookup`.

When part of a logical batch committed before a later failure, the connectors
report `UNCERTAIN` unless replay of the entire payload is demonstrably
idempotent. onestep does not automatically retry uncertain writes.

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

## Feishu Bitable

Use the `onestep-feishu-bitable` plugin when syncing rows into a Bitable table
or incrementally copying records between Bitable tables. Install the plugin in
the same environment as `onestep`; it registers the YAML resource types through
the `onestep.resources` entry point group.

```yaml
resources:
  feishu:
    type: feishu_bitable
    app_id: "${FEISHU_APP_ID}"
    app_secret: "${FEISHU_APP_SECRET}"

  feishu_orders:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${FEISHU_APP_TOKEN}"
    table_id: "${FEISHU_TABLE_ID}"
    mode: upsert
    match_fields: [order_no]
```

For MySQL to Bitable sync, keep the field mapping in Python:

```yaml
resources:
  mysql_orders:
    type: mysql_incremental
    connector: mysql_main
    table: orders
    key: id
    cursor: [updated_at, id]

  feishu_orders:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${FEISHU_APP_TOKEN}"
    table_id: "${FEISHU_TABLE_ID}"
    mode: upsert
    match_fields: [order_no]

tasks:
  - name: sync_orders
    source: mysql_orders
    emit: feishu_orders
    handler:
      ref: worker.tasks.orders:to_feishu_fields
```

For Bitable to Bitable sync:

```yaml
resources:
  source_orders:
    type: feishu_bitable_incremental
    connector: feishu
    app_token: "${SOURCE_FEISHU_APP_TOKEN}"
    table_id: "${SOURCE_FEISHU_TABLE_ID}"
    cursor_field: updated_at
    user_id_type: user_id
    batch_size: 100
    fallback_scan_page_limit: 100

  target_orders:
    type: feishu_bitable_table_sink
    connector: feishu
    app_token: "${TARGET_FEISHU_APP_TOKEN}"
    table_id: "${TARGET_FEISHU_TABLE_ID}"
    mode: upsert
    match_fields: [order_no]
    user_id_type: user_id
```

`cursor_field` is the source high-water mark for incremental reads.
`match_fields` are the target business unique fields for sink upsert. The default
upsert path does not require storing Feishu `record_id` values in MySQL.
At runtime, effective source fetch size is capped by both task `concurrency` and
source `batch_size`; set them together when debugging larger batches.
`fallback_scan_page_limit` bounds the local fallback scan used when Feishu
rejects cursor sorting.

The Bitable source emits:

```python
{
    "record_id": "recxxxx",
    "fields": {"order_no": "A001", "updated_at": "2026-06-08T10:00:00+08:00"},
}
```

The Bitable sink accepts either a direct field mapping or a `{"fields": ...}`
payload. Field names are passed through exactly as provided, including Feishu
display names.

When copying text-like fields from one Bitable table to another, Feishu may
return rich text arrays or objects. Use `feishu_bitable_text(...)` in handlers to
flatten those values before writing them to a target text field:

```python
from onestep_feishu_bitable import feishu_bitable_text


async def map_row(ctx, payload):
    fields = payload["fields"]
    return {
        "标题": feishu_bitable_text(fields.get("标题")),
        "编号": feishu_bitable_text(fields.get("编号")),
    }
```

For Feishu person fields, use `feishu_bitable_user(...)` and set
`user_id_type` to match the identifier you provide. If your source data already
has Feishu `user_id` values, configure the sink with `user_id_type: user_id`:

```python
from onestep_feishu_bitable import feishu_bitable_text, feishu_bitable_user


async def map_row(ctx, payload):
    fields = payload["fields"]
    return {
        "编号": feishu_bitable_text(fields.get("编号")),
        "负责人": feishu_bitable_user(fields.get("负责人ID")),
    }
```

`feishu_bitable_user("u_xxx")` returns `[{"id": "u_xxx"}]`, which is the value
shape expected by Feishu Bitable person fields. The helpers are exported by the
`onestep_feishu_bitable` plugin package, not by the root `onestep` package.

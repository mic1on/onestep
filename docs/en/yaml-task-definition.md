# YAML Task Definition

`onestep` treats YAML as a task-definition and wiring layer:

- YAML defines the app, resources, hooks, task runtime policy, and the Python entrypoints to call.
- Python still owns business logic such as transform, validation, enrichment, and custom hooks.

## Design Boundary

YAML is responsible for:

- `app`: name, global config, shutdown timeout, state store binding, framework log level, failure capture policy
- `reporter`: optional control-plane telemetry wiring through `onestep[control-plane]`
- `resources`: named runtime objects and their dependencies
- `hooks`: app-level startup, shutdown, and event observers
- `tasks`: source, emit, dead-letter, retry, timeout, concurrency, handler, task config, task hooks

YAML does not define:

- inline transform DSLs
- workflow graphs
- expression engines
- embedded business logic

YAML may name Python predicate callables for conditional sink routing and Python
payload transforms for individual Sinks, but business logic still lives in Python.

## Strict Check

Use strict checking when you want YAML to behave like a real contract instead of
a permissive loader:

```bash
onestep check --strict worker.yaml
```

Strict mode is intended to catch configuration drift early:

- unknown top-level fields
- unknown task, hook, reporter, and resource fields
- invalid `apiVersion` / `kind` values when they are present
- silent mixing of legacy top-level app fields with the `app:` section
- invalid `app.logging.level` values when YAML opts into framework log control
- invalid conditional `emit` route and per-Sink binding shapes

## Visualizing the Topology

`onestep render` renders a YAML target as a Mermaid flowchart — pair it with strict checks in CI, or paste the output into any Mermaid-capable documentation platform:

```bash
onestep render worker.yaml                  # mermaid by default
onestep render worker.yaml --env-file .env  # same env expansion as check
```

Task nodes carry concurrency, retry policy, and timeout; `emit` edges include transform refs, conditional routes appear as `when <predicate>`/`otherwise`, and dead letters as dashed `dead_letter` edges. Resources shared across tasks are drawn once, so chained topologies render as connected graphs.

## Framework Logging

Pure YAML workers can set the `onestep` logger namespace level directly:

```yaml
app:
  name: hello-worker
  logging:
    level: DEBUG
```

Notes:

- this only sets the `onestep` logger namespace
- it does not configure the root logger, handlers, or formatters
- `DEBUG` enables low-level framework logs such as successful sink sends
- `onestep run --log-level LEVEL` overrides this value when explicitly provided
- without a CLI override, `onestep run` preserves this value and supplies stdout output

For long-lived configs, prefer adding:

```yaml
apiVersion: onestep/v1alpha1
kind: App
```

## Failure Capture

Failure capture is disabled unless `app.failure_capture` is configured:

```yaml
app:
  name: billing-sync
  failure_capture:
    directory: ./captures
    mode: terminal
    max_bytes: 1048576
    redact_paths:
      - /body/customer/token
      - /meta/authorization
```

| Field | Required/default | Meaning |
| --- | --- | --- |
| `directory` | required | Directory for private, atomically written capture files. |
| `mode` | `terminal` | `terminal` captures effective terminal failures; `all` also captures retryable attempts. |
| `max_bytes` | `1048576` | Positive maximum encoded file size. Oversized captures fail explicitly. |
| `redact_paths` | `[]` | JSON Pointer paths under the logical `/body` and `/meta` document. |

Known secret-key names are redacted recursively in addition to configured
pointers. Capture files use the versioned `onestep/envelope-capture` schema and
preserve JSON scalars/containers plus datetime, UUID, bytes, Decimal, enum,
tuple/namedtuple, set, and frozenset values. Enum and namedtuple types must
remain importable when replayed.

The format does not stringify unsupported values. In `mode: all`, an attempt
containing an unsupported custom value logs a capture encoding error and writes
no file; task retry/dead-letter behavior continues unchanged. This avoids a
record that looks replayable but has already lost type information.

Replay a valid capture with:

```bash
onestep task replay worker.yaml --task sync_billing --envelope captures/failure.json
```

The capture schema/version and app/task identity are validated before replay.
Sink I/O remains disabled unless `--send` is explicit.

## Real Project Layout

When a team actually adopts YAML task definitions, the recommended shape is still small:

```text
your-project/
├── pyproject.toml
├── worker.yaml
└── src/
    └── your_worker/
        ├── tasks.py
        ├── transforms.py
        └── hooks.py
```

That example now exists in this repo at `example/yaml_project/`.

The rule stays the same:

- `worker.yaml` defines runtime wiring
- `tasks/` defines handlers
- `transforms/` holds business transforms
- `hooks.py` is optional and only for lifecycle or side-observer logic

If you want that shape immediately, scaffold it with:

```bash
onestep init your-project
```

`init` intentionally generates the smallest runnable project. It does not add
reporter config, hook modules, extra hooks, or more YAML structure by default.

From the repo root:

```bash
PYTHONPATH=src python -m onestep.cli check example/yaml_project/worker.yaml
PYTHONPATH=src python -m onestep.cli run example/yaml_project/worker.yaml
```

## Recommended Progression

Start with the smallest shape that runs. Add fields only when the task actually needs them.

### Level 1: Minimal Task

```yaml
app:
  name: hello-worker
  logging:
    level: DEBUG

resources:
  tick:
    type: interval
    minutes: 5
    immediate: true

tasks:
  - name: hello
    source: tick
    handler:
      ref: worker.tasks.main:hello
```

This is the default mental model:

- one app
- one source
- one handler
- no hooks
- no extra config

### Level 2: Add Passthrough Sinks

If a task only forwards the incoming payload to one or more sinks, `handler` can be omitted. The runtime will use a passthrough handler that returns the source payload unchanged.

```yaml
app:
  name: event-forwarder

resources:
  incoming:
    type: memory

  notify:
    type: http_sink
    url: "https://example.com/hooks/events"
    headers:
      X-Api-Key: "${NOTIFY_TOKEN}"

tasks:
  - name: forward_events
    source: incoming
    emit: notify
```

Strict mode still requires each task to define either `handler` or a non-empty `emit`. Use a Python handler when the payload needs transform, validation, signing, or enrichment.

### Level 3: Add Sinks And Runtime Policy

```yaml
app:
  name: user-sync

resources:
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

  mysql_main:
    type: mysql
    dsn: "${MYSQL_DSN}"

tasks:
  - name: sync_users
    source: users_source
    emit: [users_sink]
    handler:
      ref: worker.tasks.users:sync_users
    concurrency: 4
    timeout_s: 120
    retry:
      type: max_attempts
      max_attempts: 5
      delay_s: 10
```

`mysql_table_sink` fields:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `type` | required: `mysql_table_sink` | Resource type. |
| `connector` | required | Reference to a `mysql` connector. |
| `table` | required | Non-empty existing table name. |
| `mode` | `insert` | `insert` or `upsert`. |
| `keys` | required for `upsert` | Unique key columns used to detect conflicts. |
| `update_columns` | optional | For `upsert` only: whitelist of columns to update on conflict. Defaults to every payload column except `keys`. |
| `update_expr` | optional | For `upsert` only: mapping of column to a raw SQL expression rendered on conflict (for example `updated_at: NOW(6)`). |
| `serialize_json` | `auto` | `auto`, `always`, or `never`. When `auto`, list/dict payload values are JSON-serialized unless the target column is a JSON type. |

In `upsert` mode, only the fields selected by `update_columns` are rewritten on
conflict; when `update_columns` is unset, every non-key field is rewritten.
Setting `update_columns: []` disables payload updates entirely, leaving only
`update_expr` entries (for example `updated_at=NOW(6)`) to run on conflict.
`update_expr` values are rendered as raw SQL expressions. Payload values
that are lists or dicts are serialized to JSON strings before binding unless
the column type is JSON (`auto`) or serialization is forced off (`never`).

### Level 4: Add Conditional Sink Routing

`emit` entries can mix unconditional sinks with conditional route mappings.
YAML only names the predicate callable and target sinks; Python evaluates the
condition.

```yaml
tasks:
  - name: route_users
    source: users_source
    emit:
      - audit_sink
      - when:
          ref: worker.routing:is_active_user
          params:
            status_field: status
        then: active_user_sink
        otherwise: inactive_user_sink
    handler:
      ref: worker.tasks.users:normalize_user
```

The predicate callable may accept `ctx`, `payload`, and `result` positional
arguments. It can also receive keyword arguments from `when.params`.

```python
def is_active_user(ctx, payload, result, *, status_field: str) -> bool:
    return result.get(status_field) == "active"
```

Rules:

- `when` is a callable ref string or a `{ref, params}` mapping.
- `then` is a sink name, a list of sink names, or a list of emit-binding
  mappings (`{sink, transform}`) as shown in Per-Sink Payload Transforms below.
- `otherwise` is optional; when omitted, a falsy predicate skips that route.
- separate `emit` entries are evaluated independently and in order.
- within one route, only `then` or `otherwise` is selected.
- predicate exceptions are task failures and use the task retry/dead-letter policy.
- already completed sink sends are not rolled back if a later route or sink fails.

### Per-Sink Payload Transforms

Use a binding when selected sinks need different payload shapes. YAML declares
the static topology, while the Python transform owns the payload projection.

~~~yaml
tasks:
  - name: extract_entities
    source: entity_events
    emit:
      - sink: entity_callback
      - sink: downstream_meta
        transform: worker.transforms:to_meta_row
    handler:
      ref: worker.tasks:extract_entities
~~~

sink names exactly one Sink resource. transform is optional; without it, that
binding receives the handler result unchanged. A transform is a Python callable
that receives ctx, the original source payload, and the handler result; it may be
synchronous or async and returns the body for that Sink.

The transform value is either a callable ref string
(`transform: worker.transforms:to_meta_row`) or a `{ref, params}` mapping when
the callable needs call-time keyword arguments; both forms work in plain
bindings and inside `then`/`otherwise` branches.

~~~python
async def to_meta_row(ctx, payload, result):
    return {
        "id": result["document_id"],
        "address": payload["address"],
    }


def to_prefixed_row(ctx, payload, result, *, prefix: str):
    return {"id": f"{prefix}:{result['document_id']}"}
~~~

When the transform needs arguments, use the mapping form with `params`; entries
become call-time keyword arguments:

~~~yaml
tasks:
  - name: extract_entities
    source: entity_events
    emit:
      - sink: downstream_meta
        transform:
          ref: worker.transforms:to_prefixed_row
          params:
            prefix: bidding
    handler:
      ref: worker.tasks:extract_entities
~~~

OneStep evaluates every selected transform in YAML order before it sends to any
Sink. If a transform fails, no configured Sink output is sent and the task uses
its normal retry/dead-letter policy. Once dispatch begins, writes remain
at-least-once: a later Sink failure does not roll back earlier writes, so each
destination must be idempotent when duplicates matter.

A binding mapping may contain only sink and transform; it cannot combine with
when, then, or otherwise on the same entry. Bindings can appear inside the
`then` and `otherwise` branches of a conditional route, so each sink in a
branch can receive a distinct transformed payload.

```yaml
tasks:
  - name: extract_entities
    source: entity_events
    emit:
      - sink: entity_callback
      - when: worker.tasks:has_bidding_id
        then:
          - sink: meta_sink
            transform: worker.transforms:to_meta_row
          - sink: rows_sink
            transform: worker.transforms:to_bidding_row
        otherwise:
          - sink: fallback_sink
            transform: worker.transforms:to_fallback_row
    handler:
      ref: worker.tasks:extract_entities
```

The top-level `emit` list supports the same entry shapes: plain sink names, and
binding mappings with an optional `transform`.

```yaml
emit:
  - audit_sink
  - when: worker.routing:is_active
    then:
      - active_sink
      - sink: metric_sink
        transform: worker.transforms:to_metric
```

### Level 5: Add Task Config

Use `tasks[].config` for task definition data that should be visible at runtime through `ctx.task_config`.

```yaml
tasks:
  - name: sync_users
    source: users_source
    emit: [users_sink]
    config:
      dry_run: false
      target_table: dw_users
    handler:
      ref: worker.tasks.users:sync_users
      params:
        mode: upsert
```

Rule of thumb:

- `handler.params`: call-time parameters for the Python function
- `task.config`: task definition data the runtime and handler may inspect

### Level 6: Add Hooks

Only add hooks when task wiring or lifecycle behavior cannot live inside the main handler.

```yaml
hooks:
  startup:
    - ref: worker.lifecycle:on_startup
  shutdown:
    - ref: worker.lifecycle:on_shutdown

tasks:
  - name: sync_users
    source: users_source
    emit: [users_sink]
    handler:
      ref: worker.tasks.users:sync_users
    hooks:
      before:
        - ref: worker.task_hooks:before_sync_users
      on_failure:
        - ref: worker.task_hooks:on_sync_users_failed
```

### Level 7: Add Control-Plane Reporter

Use the control-plane reporter plugin only when you need control-plane telemetry. Start with the smallest shape:

```bash
pip install 'onestep[control-plane]'
```

```yaml
reporter: true
```

That means:

- load the `onestep-control-plane` reporter plugin
- resolve `base_url` and `token` from env
- default `service_name` to `app.name`

If you need explicit overrides, keep them minimal and use the same field names as `ControlPlaneReporterConfig`:

```yaml
reporter:
  base_url: https://control-plane.example.com
  token: ${ONESTEP_CONTROL_PLANE_TOKEN}
  service_name: billing-sync-worker
  service_description: Synchronizes billing data into the warehouse
```

- `service_description` is optional service-level metadata shown by the control plane.
- It can also be supplied with `ONESTEP_SERVICE_DESCRIPTION`.
- Task-level `tasks[].description` remains separate and describes an individual task.

### Level 8: Full Wiring Example

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: user-sync
  shutdown_timeout_s: 30
  state: app_state
  config:
    region: cn

reporter: true

resources:
  mysql_main:
    type: mysql
    dsn: "${MYSQL_DSN}"

  app_state:
    type: mysql_state_store
    connector: mysql_main
    table: onestep_state

  cursor_users:
    type: mysql_cursor_store
    connector: mysql_main
    table: onestep_cursor

  users_source:
    type: mysql_incremental
    connector: mysql_main
    table: users
    key: id
    cursor: [updated_at, id]
    state: cursor_users
    state_key: users-sync

  users_sink:
    type: mysql_table_sink
    connector: mysql_main
    table: dw_users
    mode: upsert
    keys: [id]

  notify_api:
    type: http_sink
    url: "${NOTIFY_URL}"
    headers:
      Authorization: "Bearer ${NOTIFY_TOKEN}"
    success_statuses: [200, 202]

  audit_stream:
    type: redis_stream
    connector: redis_main
    stream: audit:user_sync
    group: onestep

  redis_main:
    type: redis
    url: "${REDIS_URL:redis://localhost:6379}"

  users_dead:
    type: redis_stream
    connector: redis_main
    stream: dead_letter:user_sync
    group: onestep

hooks:
  startup:
    - ref: worker.lifecycle:on_startup
      params:
        preload_cache: true
  shutdown:
    - ref: worker.lifecycle:on_shutdown
  events:
    - ref: worker.observers:metrics_handler
    - ref: worker.observers:structured_logger

tasks:
  - name: sync_users
    description: Sync incremental users into DW
    source: users_source
    emit: [users_sink, audit_stream, notify_api]
    dead_letter: [users_dead]
    config:
      target_table: dw_users
      dry_run: false
    metadata:
      owner: data-platform
      tags: [users, mysql]
    handler:
      ref: worker.tasks.users:sync_users
      params:
        mode: upsert
    hooks:
      before:
        - ref: worker.task_hooks:before_sync_users
      after_success:
        - ref: worker.task_hooks:after_sync_users
      on_failure:
        - ref: worker.task_hooks:on_sync_users_failed
    concurrency: 4
    timeout_s: 120
    retry:
      type: max_attempts
      max_attempts: 5
      delay_s: 10
```

## Python Side

The business project mainly writes handlers, transforms, and optional hooks.

```python
# worker/transforms/users.py
def normalize_user(payload: dict, *, region: str) -> dict:
    return {
        "id": payload["id"],
        "name": payload["name"].strip(),
        "region": region,
    }
```

```python
# worker/tasks/users.py
from worker.transforms.users import normalize_user


async def sync_users(ctx, payload, *, mode: str):
    row = normalize_user(payload, region=ctx.config["region"])

    if ctx.task_config.get("dry_run"):
        ctx.logger.info("dry run", extra={"payload": row})
        return None

    row["mode"] = mode
    return row
```

## Runtime Access

Handlers and task hooks can use:

- `ctx.config`: app-level config from `app.config`
- `ctx.task_config`: task-level config from `tasks[].config`
- `ctx.task.config`: the same task config on the task spec
- `ctx.resources`: named runtime objects from `resources`
- `ctx.state`: per-task namespaced state

App hooks can use:

- `app.resources`: named runtime objects from `resources`
- `app.tasks`: loaded task specs
- `app.config`: app-level config

## Hook Signatures

`onestep` truncates positional arguments based on the callable signature, so hooks can choose the amount of context they need.

Supported app-level hooks:

- `startup`: `func(app)` or `func()`
- `shutdown`: `func(app)` or `func()`
- `events`: `func(event)` or `func()`

Supported task-level hooks:

- `before`: `func(ctx, payload)`, `func(ctx)`, or `func()`
- `after_success`: `func(ctx, payload, result)`, `func(ctx, payload)`, `func(ctx)`, or `func()`
- `on_failure`: `func(ctx, payload, failure)`, `func(ctx, payload)`, `func(ctx)`, or `func()`

Hook `params` are passed as keyword arguments after the runtime arguments.

## Hook Semantics

- `before` runs after the delivery starts processing and after the `started` event is emitted.
- `after_success` runs after the handler returns successfully, before emitting to sinks and before `ack()`.
- conditional `emit.when` predicates run after `after_success`, before sink sends and before `ack()`.
- `on_failure` runs for task failures before retry or dead-letter decisions are applied.
- failures inside `on_failure` hooks are logged and do not replace the original task failure.
- `timeout_s` currently applies to the async handler body itself; task hooks remain outside that timeout.

## Resource Notes

- `resources` is the preferred top-level section for named runtime objects.
- legacy `connectors`, `sources`, and `sinks` sections are still accepted and merged into the same resource registry.
- resources are available at runtime through `app.resources` and `ctx.resources`.

Built-in resource types:

- `memory`
- `interval`
- `cron`
- `webhook`
- `http_sink`

In strict mode, `memory` resources must set a positive `maxsize`; this keeps
long-lived YAML workers from creating unbounded in-process queues by accident.
Scheduled `interval` and `cron` resources accept `max_queued_runs` for
`overlap: queue`, defaulting to `1000`.

`http_sink` sends task results as JSON by default. Configure `body` only when
the outbound payload should be reshaped. `url`, `headers`, `params`, and
configured `body` values can reference `body`, `payload`, `meta`, and
`attempts` with `&#123;&#123; ... &#125;&#125;` variables.

Plugin resource types:

- `onestep-elasticsearch`: `elasticsearch`, `elasticsearch_bulk_sink`
- `onestep-clickhouse`: `clickhouse`, `clickhouse_table_sink`
- `onestep-sql`: `mysql`, `mysql_state_store`, `mysql_cursor_store`, `mysql_table_queue`, `mysql_incremental`, `mysql_table_sink`, `mysql_binlog`, `postgres`, `postgres_state_store`, `postgres_cursor_store`, `postgres_table_queue`, `postgres_incremental`, `postgres_table_sink`, `postgres_execution_source` (the legacy `onestep-mysql` / `onestep-postgres` forwarding shims still register the same types via `onestep-sql`)
- `onestep-mq`: `rabbitmq`, `rabbitmq_queue`
- `onestep-redis`: `redis`, `redis_stream`
- `onestep-sqs`: `sqs`, `sqs_queue`
- `onestep-kafka`: `kafka`, `kafka_topic`
- `onestep-feishu-bitable`: `feishu_bitable`, `feishu_bitable_incremental`, `feishu_bitable_table_sink`
- `onestep-mongodb`: `mongodb`, `mongodb_polling`, `mongodb_change_stream`, `mongodb_collection_sink`

### Elasticsearch And OpenSearch Resources

Install `onestep-elasticsearch` directly or with
`pip install 'onestep[elasticsearch]'`. One connector serves the common
Elasticsearch/OpenSearch HTTP bulk boundary:

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

`elasticsearch` fields:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `type` | required: `elasticsearch` | Resource type. |
| `hosts` | required | Non-empty HTTP(S) URL string or string list. |
| `distribution` | `auto` | `auto`, `elasticsearch`, or `opensearch`. |
| `username` | optional | Basic-auth username; requires `password`. |
| `password` | optional | Basic-auth password; requires `username`; secret. |
| `api_key` | optional | API-key credential; secret. |
| `bearer_token` | optional | Bearer credential; secret. |
| `headers` | optional | Secret mapping of custom HTTP headers. |
| `verify_certs` | `true` | Enable TLS certificate verification. |
| `ca_certs` | optional | CA bundle path. |
| `client_cert` | optional | Client certificate path. |
| `client_key` | optional | Client key path; secret. |
| `request_timeout_s` | `10.0` | Positive request timeout in seconds. |

Configure no authentication or exactly one of Basic, API key, or Bearer. The
Basic pair counts as one mode. Strict mode rejects partial Basic credentials,
multiple auth modes, invalid host schemes, unknown fields, and non-positive
timeouts without contacting either service.

`elasticsearch_bulk_sink` fields:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `type` | required: `elasticsearch_bulk_sink` | Resource type. |
| `connector` | required | Reference to an `elasticsearch` connector. |
| `index` | required | Non-empty static target index. |
| `operation` | `index` | `index` or `create`. |
| `id_field` | optional | Payload field copied to `_id` and retained in `_source`. |
| `chunk_size` | `500` | Positive maximum actions per request. |
| `max_chunk_bytes` | `5000000` | Positive maximum serialized NDJSON bytes per request. |
| `refresh` | `false` | `false`, `true`, or `wait_for`. |
| `pipeline` | optional | Static ingest pipeline name. |

The index and operation are static resource configuration, not payload-routing
fields. No Elasticsearch/OpenSearch search source is registered in this release.

### ClickHouse Resources

Install `onestep-clickhouse` directly or with
`pip install 'onestep[clickhouse]'`:

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

`clickhouse` fields:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `type` | required: `clickhouse` | Resource type. |
| `dsn` | required | Non-empty ClickHouse or HTTP(S) DSN; secret. |
| `client_options` | optional | Secret mapping passed to async client creation. |

`clickhouse_table_sink` fields:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `type` | required: `clickhouse_table_sink` | Resource type. |
| `connector` | required | Reference to a `clickhouse` connector. |
| `table` | required | Non-empty existing table name. |
| `columns` | optional | Non-empty unique list fixing row column order. |
| `batch_size` | `1000` | Positive maximum rows per insert. |
| `settings` | optional | Mapping passed to each insert. |

With configured `columns`, every row must contain all named columns and no
others. Without `columns`, the first row fixes insertion order and later rows
must have the same key set. If `settings.async_insert` is enabled, strict mode
also requires `wait_for_async_insert: 1`; fire-and-forget inserts are rejected.

### MongoDB Resources

Install `onestep-mongodb` directly or with `pip install 'onestep[mongodb]'`.
Change streams require a replica set or sharded cluster. This production example
uses an explicit durable PostgreSQL cursor store:

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
    filter:
      archived: false
    batch_size: 100
    poll_interval_s: 1
    state: events_cursor
    state_key: events-poll

  events_changes:
    type: mongodb_change_stream
    connector: mongo
    collection: events
    pipeline:
      - $match:
          operationType:
            $in: [insert, update, delete]
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

`mongodb` fields:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `type` | required: `mongodb` | Resource type. |
| `uri` | required | Non-empty MongoDB URI; secret. |
| `database` | required | Non-empty database name. |
| `client_options` | optional | Secret mapping passed to `AsyncMongoClient`. |

Strict mode rejects an unacknowledged `w=0` write concern.

`mongodb_polling` fields:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `type` | required: `mongodb_polling` | Resource type. |
| `connector` | required | Reference to a `mongodb` connector. |
| `collection` | required | Non-empty collection name. |
| `cursor` | `[_id]` | Non-empty unique field list; explicit `_id` must be final. |
| `filter` | optional | Query mapping combined with the keyset predicate. |
| `projection` | optional | Projection mapping. |
| `batch_size` | `100` | Positive maximum documents per fetch. |
| `poll_interval_s` | `1.0` | Non-negative delay between empty polls. |
| `state` | optional | Cursor-store resource reference. |
| `state_key` | optional | Persistent cursor key override. |
| `initial_cursor` | optional | JSON cursor used only when stored state is absent. |

When `_id` is not configured, it is appended as the deterministic final
tie-breaker. Polling is ascending keyset traversal, not CDC: deletes are
invisible and updates that do not advance a cursor field can be missed.
A polling projection must retain every effective cursor field unchanged,
including the implicit `_id` tie-breaker. Invalid projections fail during
resource construction.

`mongodb_change_stream` fields:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `type` | required: `mongodb_change_stream` | Resource type. |
| `connector` | required | Reference to a `mongodb` connector. |
| `collection` | required | Non-empty collection name. |
| `pipeline` | optional | JSON list of aggregation stages. |
| `full_document` | `updateLookup` | Supported PyMongo full-document option. |
| `max_await_time_ms` | `1000` | Positive server await time. |
| `batch_size` | `100` | Positive maximum events per fetch. |
| `poll_interval_s` | `0.1` | Non-negative delay after an empty fetch. |
| `state` | optional | Cursor-store resource reference. |
| `state_key` | optional | Persistent resume-token key override. |

Change streams emit complete raw MongoDB change events. Without stored state,
they start at the current server position rather than replaying collection
history. Invalid or expired resume tokens fail permanently and require an
explicit operator reset.

`mongodb_collection_sink` fields:

| Field | Required/default | Meaning |
| --- | --- | --- |
| `type` | required: `mongodb_collection_sink` | Resource type. |
| `connector` | required | Reference to a `mongodb` connector. |
| `collection` | required | Non-empty collection name. |
| `mode` | `insert` | `insert` or `upsert`. |
| `keys` | required for `upsert` | Non-empty unique key-field list. |
| `ordered` | `true` | Preserve ordered bulk-write behavior. |
| `batch_size` | `1000` | Positive maximum documents per write. |

All three database bulk sinks accept one mapping or a non-empty sequence of
mappings and await every backend chunk acknowledgement. A retry can repeat
committed chunks; use stable IDs/keys or backend dedup-aware schema design when
duplicates matter. A partial commit is classified as `UNCERTAIN` unless replay
of the complete payload is demonstrably idempotent.

MongoDB polling and change streams may use in-memory state for development.
Production restart guarantees require an explicit durable `state` cursor store.
Resume tokens and cursor values use BSON Extended JSON when stored through a
generic cursor store.

`kafka_topic` can be used as a source, sink, or both. When used as a source,
set `group_id`; the plugin disables Kafka auto commit and commits offsets only
after onestep reaches `ack()` or terminal `fail()`.

`feishu_bitable_incremental` accepts `fallback_scan_page_limit` to bound the
fallback scan used when Feishu rejects cursor sorting. The default is `100`
pages.

Install the corresponding plugin package in the worker environment before
using plugin resource types in YAML.

Additional resource types can be provided by installed packages. A package can
register YAML resources through the `onestep.resources` entry point group:

```toml
[project.entry-points."onestep.resources"]
feishu_bitable = "onestep_feishu_bitable:register"
```

The entry point receives the resource registry and registers one or more
resource handlers. Once the package is installed in the worker environment, YAML
files can use the provided `type` values without changing onestep core.

The repository includes plugin packages under `plugins/`, each with its own
entry point and release workflow.

## MySQL to Feishu insert controls

`mysql_incremental.batch_size` limits rows returned by one source fetch, and the
runtime further caps that fetch by available task concurrency.
`feishu_bitable_table_sink.batch_size` is the Feishu write boundary.
`tasks[].concurrency` is the maximum number of in-flight deliveries. These are
independent; `tasks[].config.batch_size` would only be arbitrary data exposed as
`ctx.task_config` and does not batch either connector. See
`example/mysql_feishu_insert.yaml` for the strict lowercase, durable-cursor
configuration.

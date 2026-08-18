# YAML Task Definition

Use YAML as a runtime wiring layer. Python owns business logic.

## Boundary

YAML defines:

- `app`: name, config, shutdown timeout, state store binding.
- `reporter`: built-in control-plane telemetry wiring.
- `resources`: named sources, sinks, connectors, and stores.
- `hooks`: app lifecycle hooks and event observers.
- `tasks`: source, emit, dead-letter, retry, timeout, concurrency, handler, task config, and task hooks.

YAML does not define transforms, workflow graphs, expression engines, or embedded business logic.
YAML may name Python predicate callables for conditional sink routing; the condition logic stays in Python.

## Strict Mode

For long-lived YAML, prefer:

```yaml
apiVersion: onestep/v1alpha1
kind: App
```

Validate with:

```bash
onestep check --strict worker.yaml
```

Strict mode catches unknown top-level fields, unknown task/hook/reporter/resource fields, invalid `apiVersion` and `kind`, and accidental mixing of legacy top-level app fields with `app:`.

## Recommended Layout

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

Rules:

- `worker.yaml` defines runtime wiring.
- `tasks.py` defines handlers.
- `transforms.py` holds business transforms.
- `hooks.py` is optional and only for lifecycle or side-observer logic.

## Minimal Task

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: hello-worker

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

## Sinks And Runtime Policy

```yaml
app:
  name: user-sync

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

## Conditional Sink Routing

`emit` can mix unconditional sink names with conditional route mappings:

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

Rules:

- `when` is a callable ref string or `{ref, params}` mapping.
- the predicate may accept `ctx`, `payload`, and `result` positional arguments.
- `then` and `otherwise` are sink names, lists of sink names, or lists of
  emit-binding mappings (`{sink, transform}`) as shown below.
- omitted `otherwise` means a falsy predicate skips that route.
- separate `emit` entries are evaluated independently and in order.

## Per-Sink Payload Transforms

Use a binding when one handler result must be projected into different payloads
for different static sinks:

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

Each binding names exactly one sink. Its optional transform is a Python callable
with (ctx, payload, result) and may be synchronous or async; without a transform,
that sink receives the handler result unchanged. The transform value is either a
callable ref string, or a `{ref, params}` mapping when the callable needs
call-time keyword arguments; both forms work in plain bindings and inside
`then`/`otherwise` branches. OneStep prepares all selected transform results in
YAML order before sending to any sink. A transform failure therefore sends no
configured output and follows normal task retry or dead-letter policy.

Sink dispatch remains at-least-once and non-transactional after preparation: if
a later sink fails, an earlier successful sink can receive a duplicate on retry.
Use stable business keys or sink idempotency when duplicates matter. A binding may
contain only sink and transform; do not combine it with when, then, or otherwise
on the same entry. Bindings can appear inside the `then` and `otherwise` branches
of a conditional route, so each sink in a branch can receive a distinct transformed
payload.

When the transform needs arguments, use the mapping form with `params`; entries
are passed as call-time keyword arguments:

~~~yaml
tasks:
  - name: extract_entities
    source: entity_events
    emit:
      - sink: downstream_meta
        transform:
          ref: worker.transforms:to_meta_row
          params:
            prefix: bidding
    handler:
      ref: worker.tasks:extract_entities
~~~

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

## Passthrough Tasks

If a YAML task only forwards the incoming payload to sinks, it may omit `handler`.
The runtime uses a passthrough handler that returns the source payload unchanged.

```yaml
resources:
  incoming:
    type: memory

  notify:
    type: http_sink
    url: "https://example.com/hooks/events"

tasks:
  - name: forward_events
    source: incoming
    emit: notify
```

Strict mode requires each task to define either `handler` or a non-empty `emit`.
Use a Python handler when the task must transform, validate, sign, or enrich the payload.

## Task Config Vs Handler Params

Use `tasks[].config` for task definition data visible as `ctx.task_config`.

Use `handler.params` for call-time arguments passed into the Python callable.

```yaml
tasks:
  - name: sync_users
    source: users_source
    config:
      dry_run: false
      target_table: dw_users
    handler:
      ref: worker.tasks.users:sync_users
      params:
        mode: upsert
```

## Hooks

Only add hooks when lifecycle or side-observer behavior cannot live inside the handler.

```yaml
hooks:
  startup:
    - ref: worker.lifecycle:on_startup
  shutdown:
    - ref: worker.lifecycle:on_shutdown

tasks:
  - name: sync_users
    source: users_source
    handler:
      ref: worker.tasks.users:sync_users
    hooks:
      before:
        - ref: worker.task_hooks:before_sync_users
      on_failure:
        - ref: worker.task_hooks:on_sync_users_failed
```

## Control-Plane Reporter

Only add reporter config for control-plane telemetry. The smallest YAML form is:

```yaml
reporter: true
```

This loads the `onestep-control-plane` reporter plugin and resolves `base_url` and `token` from environment variables.

## Supported Resource Types

Built-in resource types:

- `memory`
- `interval`
- `cron`
- `webhook`
- `http_sink`

Plugin resource types:

- `onestep-elasticsearch`: `elasticsearch`, `elasticsearch_bulk_sink`
- `onestep-clickhouse`: `clickhouse`, `clickhouse_table_sink`
- `onestep-mysql`: `mysql`, `mysql_state_store`, `mysql_cursor_store`, `mysql_table_queue`, `mysql_incremental`, `mysql_table_sink`
- `onestep-mq`: `rabbitmq`, `rabbitmq_queue`
- `onestep-redis`: `redis`, `redis_stream`
- `onestep-sqs`: `sqs`, `sqs_queue`
- `onestep-mongodb`: `mongodb`, `mongodb_polling`, `mongodb_change_stream`, `mongodb_collection_sink`

Full field reference and YAML examples for every plugin resource type are in [docs/yaml-task-definition.md](/docs/yaml-task-definition.md).

Resources can reference other resources by name, for example `rabbitmq_queue.connector: rmq` or `mysql_incremental.state: cursor_store`.

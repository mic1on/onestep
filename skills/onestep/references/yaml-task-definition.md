# YAML Task Definition

Use YAML as a runtime wiring layer. Python owns business logic.

## Boundary

YAML defines:

- `app`: name, config, shutdown timeout, state store binding.
- `reporter`: built-in control-plane telemetry wiring.
- `resources`: named sources, sinks, connectors, and stores.
- `hooks`: app lifecycle hooks and event observers.
- `tasks`: source, emit, dead-letter, retry, timeout, concurrency, handler, task config, and task hooks.

YAML does not define transforms, conditional branches, workflow graphs, expression engines, or embedded business logic.

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

## Built-In Reporter

Only add reporter config for control-plane telemetry. The smallest YAML form is:

```yaml
reporter: true
```

This enables `ControlPlaneReporter` and resolves `base_url` and `token` from environment variables.

## Supported Resource Types

Common stable resource types:

- `memory`
- `interval`
- `cron`
- `webhook`
- `http_sink`
- `rabbitmq`
- `rabbitmq_queue`
- `redis`
- `redis_stream`
- `sqs`
- `sqs_queue`
- `mysql`
- `mysql_state_store`
- `mysql_cursor_store`
- `mysql_table_queue`
- `mysql_incremental`
- `mysql_table_sink`

Resources can reference other resources by name, for example `rabbitmq_queue.connector: rmq` or `mysql_incremental.state: cursor_store`.

# YAML Task Definition

`onestep` treats YAML as a task-definition and wiring layer:

- YAML defines the app, resources, hooks, task runtime policy, and the Python entrypoints to call.
- Python still owns business logic such as transform, validation, enrichment, and custom hooks.

## Design Boundary

YAML is responsible for:

- `app`: name, global config, shutdown timeout, state store binding
- `reporter`: built-in control-plane telemetry wiring
- `resources`: named runtime objects and their dependencies
- `hooks`: app-level startup, shutdown, and event observers
- `tasks`: source, emit, dead-letter, retry, timeout, concurrency, handler, task config, task hooks

YAML does not define:

- transform DSLs
- conditional branches or workflow graphs
- expression engines
- embedded business logic

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

For long-lived configs, prefer adding:

```yaml
apiVersion: onestep/v1alpha1
kind: App
```

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

### Level 2: Add Sinks And Runtime Policy

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

### Level 3: Add Task Config

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

### Level 4: Add Hooks

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

### Level 5: Add Built-In Reporter

Use the built-in reporter only when you need control-plane telemetry. Start with the smallest shape:

```yaml
reporter: true
```

That means:

- enable `ControlPlaneReporter`
- resolve `base_url` and `token` from env
- default `service_name` to `app.name`

If you need explicit overrides, keep them minimal and use the same field names as `ControlPlaneReporterConfig`:

```yaml
reporter:
  base_url: https://control-plane.example.com
  token: ${ONESTEP_CONTROL_PLANE_TOKEN}
  service_name: billing-sync-worker
```

### Level 6: Full Wiring Example

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
    emit: [users_sink, audit_stream]
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
- `on_failure` runs for task failures before retry or dead-letter decisions are applied.
- failures inside `on_failure` hooks are logged and do not replace the original task failure.
- `timeout_s` currently applies to the async handler body itself; task hooks remain outside that timeout.

## Resource Notes

- `resources` is the preferred top-level section for named runtime objects.
- legacy `connectors`, `sources`, and `sinks` sections are still accepted and merged into the same resource registry.
- resources are available at runtime through `app.resources` and `ctx.resources`.

Supported resource types today:

- `memory`
- `interval`
- `cron`
- `webhook`
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

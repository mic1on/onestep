---
name: onestep
description: Use when building, modifying, validating, or explaining onestep async task runtime apps, YAML task definitions, queue/polling/schedule/webhook workers, connectors, retry/dead-letter policy, and control-plane reporter integration.
---

# onestep

## Core Model

onestep is a small async task runtime. Keep this mental model:

- `OneStepApp`: task registry and lifecycle manager.
- `Source`: fetches work from a queue, polling backend, schedule, or webhook.
- `Sink`: publishes processed output.
- `Delivery`: a fetched item with `payload`, `ack()`, `retry()`, and `fail()`.

Default to the smallest working task. Add hooks, reporter config, dead-letter sinks, retries, and connector-specific options only when the user asks for them or the workload clearly needs them.

## Workflow

1. Identify whether the user wants a Python app, YAML task definition, connector wiring, control-plane telemetry, migration help, or tests.
2. Read only the relevant reference file:
   - New app or basic usage: `references/quickstart.md`
   - YAML worker definitions: `references/yaml-task-definition.md`
   - Python API usage: `references/python-api.md`
   - MySQL/RabbitMQ/Redis/SQS/webhook/HTTP sink/schedule wiring: `references/connectors.md`
   - Control-plane reporter or WebSocket telemetry: `references/control-plane.md`
   - Tests and validation commands: `references/testing.md`
   - Migration from old `step` / broker APIs: `references/migration-0.5-to-1.0.md`
3. Make surgical changes in the user's project. Match the existing style and do not introduce speculative structure.
4. Validate with `onestep check` for app targets, `onestep check --strict` for long-lived YAML, and focused `pytest` when code behavior changed.

## Defaults

- Prefer YAML for runtime wiring and Python for business logic.
- Do not put transforms, branching logic, or workflow DSLs in YAML.
- For pure forwarding tasks, YAML may omit `handler` when `emit` is configured.
- Use `tasks[].config` for task definition data available as `ctx.task_config`.
- Use `handler.params` for call-time arguments passed into a handler or hook.
- Do not enable `reporter` or control-plane integration unless explicitly needed.
- Keep generated projects minimal; `onestep init <project>` is the preferred scaffold when the CLI is available.

## Minimal Python App

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, item):
    print("syncing billing data")
```

Run or check it with:

```bash
onestep check your_package.tasks:app
onestep run your_package.tasks:app
```

## Minimal YAML App

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: billing-sync

resources:
  tick:
    type: interval
    minutes: 5
    immediate: true

tasks:
  - name: sync_billing
    source: tick
    handler:
      ref: your_package.tasks.billing:sync_billing
```

Validate it with:

```bash
onestep check --strict worker.yaml
```

## Bundled Helpers

- `scripts/scaffold_worker.py`: creates a minimal worker project, using `onestep init` when available.
- `scripts/check_worker.py`: runs strict YAML checks and optional pytest for a worker project.
- `assets/yaml-project-template/`: fallback minimal YAML worker template.

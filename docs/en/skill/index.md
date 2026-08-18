---
title: SKILL | OneStep
outline: deep
---

# SKILL

`skills/onestep/` provides OneStep workflow documentation and reusable resources for AI coding agents. It is not a runtime API and is never loaded by application processes; it is used by Codex, Claude, and other agents to follow consistent boundaries and commands when generating, modifying, or validating OneStep workers.

## Installation

Install the OneStep Skill from the repository using the `skills` CLI:

```bash
npx skills add mic1on/onestep --skill onestep
```

After installation, the agent reads these instructions and references when working on tasks involving OneStep applications, YAML workers, connectors, and Control Plane integration.

## When to Use

Agents should read the OneStep Skill first when the task involves:

- Creating or modifying a `OneStepApp` application.
- Writing `worker.yaml`, resource definitions, task handlers, or hooks.
- Configuring Memory, Cron, Webhook, HTTP Sink, RabbitMQ, Redis Streams, AWS SQS, MySQL, or other connectors.
- Adding retry, dead letter, concurrency, timeout, or Control Plane reporter.
- Migrating from the legacy `step` / broker API to the current runtime.
- Selecting validation commands and test scope for a worker.

## Directory Structure

```text
skills/onestep/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── assets/
│   └── yaml-project-template/
├── references/
│   ├── quickstart.md
│   ├── yaml-task-definition.md
│   ├── python-api.md
│   ├── connectors.md
│   ├── control-plane.md
│   ├── testing.md
│   └── migration-0.5-to-1.0.md
└── scripts/
    ├── scaffold_worker.py
    └── check_worker.py
```

`SKILL.md` is the entry file, defining the agent's decision flow, default trade-offs, and minimal examples. `references/` splits resources by topic—agents only need to read the files relevant to the current task. `assets/yaml-project-template/` is the fallback scaffold template, and `scripts/` provides helper commands for local worker creation and validation.

## Working Principles

The OneStep Skill's default trade-offs keep workers lean:

- Prefer YAML for runtime wiring, Python for business logic.
- Don't push transforms, conditional branching, workflow DSLs, or expression engines into YAML.
- When only forwarding payloads, YAML tasks can omit `handler` and just configure `emit`.
- `tasks[].config` holds task definition data, read at runtime via `ctx.task_config`.
- `handler.params` passes parameters when invoking Python handlers or hooks.
- Don't enable reporter, hooks, dead letter, complex retry, or extra resources by default unless the task needs them.
- Long-lived YAML files use `apiVersion: onestep/v1alpha1`, `kind: App`, and strict validation.

## Minimal Python Worker

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True, overlap="skip"))
async def sync_billing(ctx, item):
    print("syncing billing data")
```

Check or run:

```bash
onestep check your_package.tasks:app
onestep run your_package.tasks:app
```

## Minimal YAML Worker

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

For long-lived or AI-modified YAML, use strict validation:

```bash
onestep check --strict worker.yaml
```

## YAML Passthrough

When a task only needs to forward source payloads as-is to a sink, `handler` can be omitted:

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

When transforms, signing, validation, or additional fields are needed, use a Python handler.

## Helper Scripts

Helper scripts in the Skill can be run from the repository root:

```bash
python skills/onestep/scripts/scaffold_worker.py ./billing-sync
python skills/onestep/scripts/check_worker.py ./billing-sync --pytest
```

`scaffold_worker.py` first tries the installed `onestep init`. If the `onestep` CLI is not available in the current environment, it falls back to the minimal template in `assets/yaml-project-template/`.

`check_worker.py` by default runs on YAML workers:

```bash
onestep check --strict worker.yaml
```

To validate a Python app target, pass it explicitly:

```bash
python skills/onestep/scripts/check_worker.py . --app-target your_package.tasks:app
```

## Related Docs

- [Quick Start](/guide/): Installation, running, and your first task for general users.
- [YAML Task Definition](/yaml-task-definition): Full YAML boundaries, fields, and strict validation.
- [Connector Overview](/broker/): Memory, Cron, Webhook, HTTP Sink, RabbitMQ, Redis, SQS, and MySQL.
- [Control Plane](/control-plane/): Runtime telemetry and WebSocket control plane integration.

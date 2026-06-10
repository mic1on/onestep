# Quickstart

Use this reference when creating the first working onestep app or explaining the basic development loop.

## Install

Core package:

```bash
pip install onestep
```

Common extras and connector plugins:

```bash
pip install 'onestep[yaml]'
pip install onestep-mysql
pip install onestep-rabbitmq
pip install onestep-redis
pip install onestep-sqs
pip install 'onestep[control-plane]'
pip install 'onestep[all]'
```

From a source checkout:

```bash
pip install -e .
pip install -e '.[dev]'
```

## Smallest Python App

```python
from onestep import IntervalSource, OneStepApp

app = OneStepApp("hello-worker")


@app.task(source=IntervalSource.every(minutes=5, immediate=True, overlap="skip"))
async def hello(ctx, item):
    print("hello from onestep")
```

Check and run:

```bash
onestep check worker.tasks:app
onestep run worker.tasks:app
```

The shorthand run form is also supported:

```bash
onestep worker.tasks:app
```

## Smallest YAML App

`worker.yaml`:

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
      ref: worker.tasks:hello
```

`src/worker/tasks.py`:

```python
async def hello(ctx, item):
    print("hello from onestep")
```

Check and run:

```bash
onestep check --strict worker.yaml
onestep run worker.yaml
```

## Scaffold

When starting from scratch, prefer:

```bash
onestep init billing-sync
```

This creates a minimal project with `pyproject.toml`, `worker.yaml`, `src/<package>/tasks/`, and `src/<package>/transforms/`. Do not add hook modules, reporter config, or extra resources unless the task needs them.

## Common Target Forms

```bash
onestep check your_package.tasks:app
onestep check your_package.tasks:build_app
onestep check worker.yaml
onestep check --json worker.yaml
onestep check --strict worker.yaml
```

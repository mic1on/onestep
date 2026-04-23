# YAML Project Demo

This directory shows the intended shape of a real `onestep` business project:

- `worker.yaml` defines the app, resources, and task wiring
- `src/demo_worker/tasks.py` owns handlers
- `src/demo_worker/transforms.py` owns business transforms
- `src/demo_worker/hooks.py` owns lifecycle and task hooks

From the repo root you can run:

```bash
PYTHONPATH=src python -m onestep.cli check example/yaml_project/worker.yaml
PYTHONPATH=src python -m onestep.cli run example/yaml_project/worker.yaml
```

Inside a standalone project, after installing `onestep[yaml]`, the same worker can
be started with:

```bash
onestep check worker.yaml
onestep run worker.yaml
```

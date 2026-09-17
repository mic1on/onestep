---
title: Worker Runtime Image | Guide
outline: deep
---

# Worker Runtime Image

onestep provides an official worker runtime image, suitable for running workers with YAML as the entry point. The image installs workspace dependencies on startup, runs `onestep check`, then executes `onestep run`.

## Required Environment Variables

| Variable | Description |
|---|---|
| `ONESTEP_TARGET` | YAML file path or Python import target |
| `WORKSPACE_DIR` | Workspace path, defaults to `/workspace` |

The full set of variables read by the image entrypoint is in [Environment Variables](/en/guide/environment-variables#worker-runtime-image).

## Mounting the Workspace

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/mic1on/onestep-worker:1.12.0
```

Startup sequence:

1. Adds `/workspace` and `/workspace/src` to `PYTHONPATH`
2. If `/workspace/requirements.txt` exists, installs those dependencies
3. Otherwise if `/workspace/pyproject.toml` exists, installs the current project
4. Runs `onestep check "$ONESTEP_TARGET"`
5. Runs `onestep run "$ONESTEP_TARGET"`

The image comes with `onestep[all]` pre-installed, including common plugin packages for RabbitMQ, Redis, MySQL, PostgreSQL, SQS, Cloudflare Queues, Kafka, MongoDB, Elasticsearch/OpenSearch, ClickHouse, and the control-plane reporter. If your YAML uses additional plugin resource types, ensure `requirements.txt` or `pyproject.toml` includes the corresponding plugin, e.g., `onestep-feishu-bitable`.

### When the workspace has a pyproject.toml

The entrypoint already adds `/workspace` to `PYTHONPATH`, so modules referenced by `handler.ref` are importable without installation; `pyproject.toml` is only there to declare extra dependencies. Be aware that step 3 runs `pip install /workspace`, building the whole workspace as a Python project. If the workspace root has multiple top-level modules side by side (e.g., `handler.py` and `client.py`), setuptools auto-discovery refuses to build:

```text
error: Multiple top-level modules discovered in a flat-layout: ['handler', 'client'].
```

Fix it in either way:

1. **Switch to `requirements.txt` (recommended)**: the entrypoint prefers it, and a plain dependency install never triggers a project build.
2. **Declare explicitly that nothing should be packaged** in `pyproject.toml`:

   ```toml
   [tool.setuptools]
   packages = []
   py-modules = []
   ```

3. **If you do have an installable package**: move the modules into a package directory with `__init__.py` and limit discovery with an `include` under `[tool.setuptools.packages.find]`.

## Custom Image

```dockerfile
FROM ghcr.io/mic1on/onestep-worker:1.12.0

WORKDIR /workspace
COPY . /workspace
ENV ONESTEP_TARGET=/workspace/worker.yaml
```

Build and run:

```bash
docker build -t my-worker .
docker run --rm my-worker
```

## Troubleshooting

| Symptom | Resolution |
|---|---|
| `ONESTEP_TARGET is required` | Set `ONESTEP_TARGET` |
| `target file is not readable` | Check mount path, `WORKSPACE_DIR`, and target file |
| Dependency installation fails | Check `requirements.txt` or `pyproject.toml` |
| `Multiple top-level modules discovered in a flat-layout` | The workspace root has multiple top-level modules; switch to `requirements.txt` or declare `packages` explicitly in `pyproject.toml` (see above) |
| `onestep check` fails | Run the same target locally and fix the YAML or import error |

## Next Steps

- [Production Deploy](/en/guide/deploy) - systemd and CLI deployment
- [YAML Task Definition](/en/yaml-task-definition) - writing worker.yaml
- [Connectors](/en/broker/) - choosing plugin resource types

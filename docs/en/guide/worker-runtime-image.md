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

## Mounting the Workspace

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/mic1on/onestep-worker:1.9.0
```

Startup sequence:

1. Adds `/workspace` and `/workspace/src` to `PYTHONPATH`
2. If `/workspace/requirements.txt` exists, installs those dependencies
3. Otherwise if `/workspace/pyproject.toml` exists, installs the current project
4. Runs `onestep check "$ONESTEP_TARGET"`
5. Runs `onestep run "$ONESTEP_TARGET"`

The image comes with `onestep[all]` pre-installed, including common plugin packages for RabbitMQ, Redis, MySQL, PostgreSQL, SQS, Kafka, and the control-plane reporter. If your YAML uses additional plugin resource types, ensure `requirements.txt` or `pyproject.toml` includes the corresponding plugin, e.g., `onestep-feishu-bitable`.

## Custom Image

```dockerfile
FROM ghcr.io/mic1on/onestep-worker:1.9.0

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
| `onestep check` fails | Run the same target locally and fix the YAML or import error |

## Next Steps

- [Production Deploy](/en/guide/deploy) - systemd and CLI deployment
- [YAML Task Definition](/en/yaml-task-definition) - writing worker.yaml
- [Connectors](/en/broker/) - choosing plugin resource types

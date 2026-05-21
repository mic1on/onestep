# Worker Runtime Image

The official worker image packages `onestep[all]` plus a small startup entrypoint.

## Required environment

- `ONESTEP_TARGET`: YAML file path or Python import target to start

## Mode A: mounted workspace

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/repository-owner/onestep-worker:1.2.62
```

Behavior:

- adds `/workspace` and `/workspace/src` to `PYTHONPATH`
- installs `/workspace/requirements.txt` when present
- otherwise installs `/workspace` when `/workspace/pyproject.toml` exists
- runs `onestep check` before `onestep run`

## Mode B: derived image

```dockerfile
FROM ghcr.io/repository-owner/onestep-worker:1.2.62

WORKDIR /workspace
COPY . /workspace
ENV ONESTEP_TARGET=/workspace/worker.yaml
```

Then build and run:

```bash
docker build -t my-worker .
docker run --rm my-worker
```

## Troubleshooting

- `ONESTEP_TARGET is required`: add the environment variable
- `target file is not readable`: fix the mounted path or `WORKDIR`
- dependency install failure: verify `requirements.txt` or `pyproject.toml` inside `/workspace`
- `onestep check` failure: run the same target locally to inspect YAML or import errors

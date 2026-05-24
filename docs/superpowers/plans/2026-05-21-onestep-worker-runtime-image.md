# Onestep Worker Runtime Image Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship an official multi-platform `onestep` worker runtime image that supports direct mounted-workspace usage, derived-image usage, CI smoke coverage, and GHCR publishing from the existing release flow.

**Architecture:** Add a small Docker runtime surface under `docker/worker/` with a shell entrypoint that owns preflight validation, `PYTHONPATH` setup, optional workspace dependency installation, and `onestep check/run`. Validate that runtime through a repo-local smoke script plus fixed test assets, then wire that smoke path into branch CI and tag-based GHCR publishing.

**Tech Stack:** Docker, POSIX shell, GitHub Actions, Python packaging, pytest fixtures already present in the repo, existing `onestep` CLI/runtime

---

## File Structure

- `docker/worker/Dockerfile`
  Builds the official runtime image from the repo source checkout and installs `onestep[all]`.
- `docker/worker/entrypoint.sh`
  Implements the container startup contract: preflight, `PYTHONPATH`, dependency discovery, `onestep check`, `onestep run`.
- `scripts/run-worker-image-smoke.sh`
  Single local/CI smoke harness for failure cases, Mode A mounted workspaces, and Mode B derived images.
- `tests/assets/worker_image/invalid_yaml/worker.yaml`
  Fixed broken YAML fixture for preflight/check failure coverage.
- `tests/assets/worker_image/mounted_requirements/*`
  Mounted workspace fixture that exercises the `requirements.txt` branch.
- `tests/assets/worker_image/mounted_pyproject/*`
  Mounted workspace fixture that exercises the `pyproject.toml` fallback branch.
- `tests/assets/worker_image/derived/Dockerfile`
  Derived-image fixture that validates the `FROM ghcr.io/.../onestep-worker` workflow locally against a test tag.
- `.github/workflows/test.yml`
  Adds Docker asset path triggers and a worker-image smoke job in normal CI.
- `.github/workflows/release.yml`
  Reuses the release tag flow to smoke-test and publish the multi-platform worker image to GHCR.
- `README.md`
  Adds discoverable, top-level user guidance for the official worker image.
- `deploy/worker-runtime-image.md`
  Holds the full A/B usage guide and troubleshooting notes.
- `deploy/README.md`
  Links the new worker image guide from the deployment index.

### Task 1: Add the runtime image skeleton and preflight smoke coverage

**Files:**
- Create: `docker/worker/Dockerfile`
- Create: `docker/worker/entrypoint.sh`
- Create: `scripts/run-worker-image-smoke.sh`
- Create: `tests/assets/worker_image/invalid_yaml/worker.yaml`

- [ ] **Step 1: Write the failing smoke harness**

Create `tests/assets/worker_image/invalid_yaml/worker.yaml` with deliberately broken YAML:

```yaml
app:
  name: broken-worker
tasks: [
```

Create `scripts/run-worker-image-smoke.sh` with the initial failure-path-only harness:

```bash
#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="onestep-worker-local:test"

docker build -f docker/worker/Dockerfile -t "$IMAGE_TAG" .

missing_target_output="$(mktemp)"
if docker run --rm "$IMAGE_TAG" >"$missing_target_output" 2>&1; then
  echo "expected container without ONESTEP_TARGET to fail" >&2
  exit 1
fi
grep -F "ONESTEP_TARGET is required" "$missing_target_output"

invalid_yaml_output="$(mktemp)"
if docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD/tests/assets/worker_image/invalid_yaml:/workspace" \
  "$IMAGE_TAG" >"$invalid_yaml_output" 2>&1; then
  echo "expected invalid YAML worker to fail" >&2
  exit 1
fi
grep -F "onestep-worker: running check for /workspace/worker.yaml" "$invalid_yaml_output"
```

- [ ] **Step 2: Run the smoke harness to verify it fails**

Run:

```bash
chmod +x scripts/run-worker-image-smoke.sh
./scripts/run-worker-image-smoke.sh
```

Expected:

- Docker build fails because `docker/worker/Dockerfile` does not exist yet

- [ ] **Step 3: Write the minimal runtime image implementation**

Create `docker/worker/Dockerfile`:

```dockerfile
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md /tmp/onestep/
COPY src/onestep /tmp/onestep/src/onestep

RUN python -m pip install --upgrade pip \
    && python -m pip install "/tmp/onestep[all]" \
    && rm -rf /tmp/onestep

COPY docker/worker/entrypoint.sh /usr/local/bin/onestep-worker-entrypoint
RUN chmod +x /usr/local/bin/onestep-worker-entrypoint

ENTRYPOINT ["onestep-worker-entrypoint"]
```

Create `docker/worker/entrypoint.sh` with preflight, `PYTHONPATH`, and check/run behavior:

```sh
#!/bin/sh
set -eu

WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
: "${ONESTEP_TARGET:?ONESTEP_TARGET is required}"

append_pythonpath() {
  if [ -n "${PYTHONPATH:-}" ]; then
    PYTHONPATH="$1:$PYTHONPATH"
  else
    PYTHONPATH="$1"
  fi
  export PYTHONPATH
}

append_pythonpath "$WORKSPACE_DIR"

if [ -d "$WORKSPACE_DIR/src" ]; then
  append_pythonpath "$WORKSPACE_DIR/src"
fi

cd "$WORKSPACE_DIR"

case "$ONESTEP_TARGET" in
  /*|./*|../*|*.yaml|*.yml)
    if [ ! -r "$ONESTEP_TARGET" ]; then
      echo "onestep-worker: target file is not readable: $ONESTEP_TARGET" >&2
      exit 1
    fi
    ;;
esac

echo "onestep-worker: workspace=$WORKSPACE_DIR"
echo "onestep-worker: target=$ONESTEP_TARGET"
echo "onestep-worker: pythonpath=$PYTHONPATH"
echo "onestep-worker: running check for $ONESTEP_TARGET"
onestep check "$ONESTEP_TARGET"
echo "onestep-worker: starting run for $ONESTEP_TARGET"
exec onestep run "$ONESTEP_TARGET"
```

- [ ] **Step 4: Run the smoke harness to verify it passes**

Run:

```bash
./scripts/run-worker-image-smoke.sh
```

Expected:

- the no-target container exits non-zero and prints `ONESTEP_TARGET is required`
- the invalid-YAML container exits non-zero after printing `onestep-worker: running check for /workspace/worker.yaml`

- [ ] **Step 5: Commit**

```bash
git add docker/worker/Dockerfile docker/worker/entrypoint.sh scripts/run-worker-image-smoke.sh tests/assets/worker_image/invalid_yaml/worker.yaml
git commit -m "feat: add worker runtime image skeleton"
```

### Task 2: Support mounted workspaces, dependency discovery, and Mode A smoke coverage

**Files:**
- Modify: `docker/worker/entrypoint.sh`
- Modify: `scripts/run-worker-image-smoke.sh`
- Create: `tests/assets/worker_image/mounted_requirements/pyproject.toml`
- Create: `tests/assets/worker_image/mounted_requirements/requirements.txt`
- Create: `tests/assets/worker_image/mounted_requirements/src/worker_image_demo/__init__.py`
- Create: `tests/assets/worker_image/mounted_requirements/src/worker_image_demo/tasks.py`
- Create: `tests/assets/worker_image/mounted_requirements/worker.yaml`
- Create: `tests/assets/worker_image/mounted_pyproject/pyproject.toml`
- Create: `tests/assets/worker_image/mounted_pyproject/src/worker_image_demo_pyproject/__init__.py`
- Create: `tests/assets/worker_image/mounted_pyproject/src/worker_image_demo_pyproject/tasks.py`
- Create: `tests/assets/worker_image/mounted_pyproject/worker.yaml`

- [ ] **Step 1: Write the failing Mode A smoke fixtures and assertions**

Create `tests/assets/worker_image/mounted_requirements/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "worker-image-mounted-requirements"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]
```

Create `tests/assets/worker_image/mounted_requirements/requirements.txt`:

```text
-e /workspace
```

Create `tests/assets/worker_image/mounted_requirements/src/worker_image_demo/__init__.py`:

```python
MARKER = "mounted-requirements"
```

Create `tests/assets/worker_image/mounted_requirements/src/worker_image_demo/tasks.py`:

```python
from __future__ import annotations

import json

from worker_image_demo import MARKER


async def run_once(ctx, payload):
    print(json.dumps({"marker": MARKER, "payload": payload}, ensure_ascii=False))
    ctx.app.request_shutdown()
```

Create `tests/assets/worker_image/mounted_requirements/worker.yaml`:

```yaml
app:
  name: mounted-requirements

resources:
  tick:
    type: interval
    seconds: 60
    immediate: true
    payload:
      source: mounted-requirements

tasks:
  - name: run_once
    source: tick
    handler:
      ref: worker_image_demo.tasks:run_once
```

Create `tests/assets/worker_image/mounted_pyproject/pyproject.toml`:

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "worker-image-mounted-pyproject"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = []

[tool.setuptools.packages.find]
where = ["src"]
```

Create `tests/assets/worker_image/mounted_pyproject/src/worker_image_demo_pyproject/__init__.py`:

```python
MARKER = "mounted-pyproject"
```

Create `tests/assets/worker_image/mounted_pyproject/src/worker_image_demo_pyproject/tasks.py`:

```python
from __future__ import annotations

import json

from worker_image_demo_pyproject import MARKER


async def run_once(ctx, payload):
    print(json.dumps({"marker": MARKER, "payload": payload}, ensure_ascii=False))
    ctx.app.request_shutdown()
```

Create `tests/assets/worker_image/mounted_pyproject/worker.yaml`:

```yaml
app:
  name: mounted-pyproject

resources:
  tick:
    type: interval
    seconds: 60
    immediate: true
    payload:
      source: mounted-pyproject

tasks:
  - name: run_once
    source: tick
    handler:
      ref: worker_image_demo_pyproject.tasks:run_once
```

Extend `scripts/run-worker-image-smoke.sh` with two new assertions:

```bash
requirements_output="$(mktemp)"
docker run --rm \
  -e ONESTEP_TARGET=worker.yaml \
  -v "$PWD/tests/assets/worker_image/mounted_requirements:/workspace" \
  "$IMAGE_TAG" >"$requirements_output" 2>&1
grep -F "onestep-worker: installing requirements from /workspace/requirements.txt" "$requirements_output"
grep -F '"marker": "mounted-requirements"' "$requirements_output"

pyproject_output="$(mktemp)"
docker run --rm \
  -e ONESTEP_TARGET=worker.yaml \
  -v "$PWD/tests/assets/worker_image/mounted_pyproject:/workspace" \
  "$IMAGE_TAG" >"$pyproject_output" 2>&1
grep -F "onestep-worker: installing project from /workspace/pyproject.toml" "$pyproject_output"
grep -F '"marker": "mounted-pyproject"' "$pyproject_output"
```

- [ ] **Step 2: Run the smoke harness to verify it fails**

Run:

```bash
./scripts/run-worker-image-smoke.sh
```

Expected:

- the new mounted workspace cases fail because the entrypoint does not install `requirements.txt`
- the new mounted workspace cases fail because the entrypoint does not install the workspace project from `pyproject.toml`

- [ ] **Step 3: Write the minimal dependency-discovery implementation**

Update `docker/worker/entrypoint.sh` so the final file is:

```sh
#!/bin/sh
set -eu

WORKSPACE_DIR="${WORKSPACE_DIR:-/workspace}"
: "${ONESTEP_TARGET:?ONESTEP_TARGET is required}"

append_pythonpath() {
  if [ -n "${PYTHONPATH:-}" ]; then
    PYTHONPATH="$1:$PYTHONPATH"
  else
    PYTHONPATH="$1"
  fi
  export PYTHONPATH
}

append_pythonpath "$WORKSPACE_DIR"

if [ -d "$WORKSPACE_DIR/src" ]; then
  append_pythonpath "$WORKSPACE_DIR/src"
fi

cd "$WORKSPACE_DIR"

case "$ONESTEP_TARGET" in
  /*|./*|../*|*.yaml|*.yml)
    if [ ! -r "$ONESTEP_TARGET" ]; then
      echo "onestep-worker: target file is not readable: $ONESTEP_TARGET" >&2
      exit 1
    fi
    ;;
esac

echo "onestep-worker: workspace=$WORKSPACE_DIR"
echo "onestep-worker: target=$ONESTEP_TARGET"
echo "onestep-worker: pythonpath=$PYTHONPATH"

if [ -f "$WORKSPACE_DIR/requirements.txt" ]; then
  echo "onestep-worker: installing requirements from $WORKSPACE_DIR/requirements.txt"
  python -m pip install --no-build-isolation -r "$WORKSPACE_DIR/requirements.txt"
elif [ -f "$WORKSPACE_DIR/pyproject.toml" ]; then
  echo "onestep-worker: installing project from $WORKSPACE_DIR/pyproject.toml"
  python -m pip install --no-build-isolation "$WORKSPACE_DIR"
else
  echo "onestep-worker: no workspace dependency file found"
fi

echo "onestep-worker: running check for $ONESTEP_TARGET"
onestep check "$ONESTEP_TARGET"
echo "onestep-worker: starting run for $ONESTEP_TARGET"
exec onestep run "$ONESTEP_TARGET"
```

- [ ] **Step 4: Run the smoke harness to verify it passes**

Run:

```bash
./scripts/run-worker-image-smoke.sh
```

Expected:

- the `requirements.txt` fixture logs `installing requirements` and prints `mounted-requirements`
- the `pyproject.toml` fixture logs `installing project` and prints `mounted-pyproject`
- the original failure-path checks still pass

- [ ] **Step 5: Commit**

```bash
git add docker/worker/entrypoint.sh scripts/run-worker-image-smoke.sh tests/assets/worker_image/mounted_requirements tests/assets/worker_image/mounted_pyproject
git commit -m "feat: support mounted worker workspaces"
```

### Task 3: Validate the derived-image workflow for Mode B

**Files:**
- Modify: `scripts/run-worker-image-smoke.sh`
- Create: `tests/assets/worker_image/derived/Dockerfile`

- [ ] **Step 1: Write the failing Mode B smoke case**

Extend `scripts/run-worker-image-smoke.sh` with a derived-image assertion that references a not-yet-created fixture Dockerfile:

```bash
DERIVED_TAG="onestep-worker-derived:test"

docker build \
  -f tests/assets/worker_image/derived/Dockerfile \
  -t "$DERIVED_TAG" \
  .

derived_output="$(mktemp)"
docker run --rm "$DERIVED_TAG" >"$derived_output" 2>&1
grep -F '"marker": "mounted-pyproject"' "$derived_output"
grep -F "onestep-worker: target=/workspace/worker.yaml" "$derived_output"
```

- [ ] **Step 2: Run the smoke harness to verify it fails**

Run:

```bash
./scripts/run-worker-image-smoke.sh
```

Expected:

- the new derived-image build fails because `tests/assets/worker_image/derived/Dockerfile` does not exist yet

- [ ] **Step 3: Write the minimal Mode B support**

Create `tests/assets/worker_image/derived/Dockerfile`:

```dockerfile
FROM onestep-worker-local:test

WORKDIR /workspace
COPY tests/assets/worker_image/mounted_pyproject /workspace
ENV ONESTEP_TARGET=/workspace/worker.yaml
```

Update `scripts/run-worker-image-smoke.sh` so the final file is:

```bash
#!/usr/bin/env bash
set -euo pipefail

IMAGE_TAG="onestep-worker-local:test"
DERIVED_TAG="onestep-worker-derived:test"

docker build -f docker/worker/Dockerfile -t "$IMAGE_TAG" .

missing_target_output="$(mktemp)"
if docker run --rm "$IMAGE_TAG" >"$missing_target_output" 2>&1; then
  echo "expected container without ONESTEP_TARGET to fail" >&2
  exit 1
fi
grep -F "ONESTEP_TARGET is required" "$missing_target_output"

invalid_yaml_output="$(mktemp)"
if docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD/tests/assets/worker_image/invalid_yaml:/workspace" \
  "$IMAGE_TAG" >"$invalid_yaml_output" 2>&1; then
  echo "expected invalid YAML worker to fail" >&2
  exit 1
fi
grep -F "onestep-worker: running check for /workspace/worker.yaml" "$invalid_yaml_output"

requirements_output="$(mktemp)"
docker run --rm \
  -e ONESTEP_TARGET=worker.yaml \
  -v "$PWD/tests/assets/worker_image/mounted_requirements:/workspace" \
  "$IMAGE_TAG" >"$requirements_output" 2>&1
grep -F "onestep-worker: installing requirements from /workspace/requirements.txt" "$requirements_output"
grep -F '"marker": "mounted-requirements"' "$requirements_output"

pyproject_output="$(mktemp)"
docker run --rm \
  -e ONESTEP_TARGET=worker.yaml \
  -v "$PWD/tests/assets/worker_image/mounted_pyproject:/workspace" \
  "$IMAGE_TAG" >"$pyproject_output" 2>&1
grep -F "onestep-worker: installing project from /workspace/pyproject.toml" "$pyproject_output"
grep -F '"marker": "mounted-pyproject"' "$pyproject_output"

docker build \
  -f tests/assets/worker_image/derived/Dockerfile \
  -t "$DERIVED_TAG" \
  .

derived_output="$(mktemp)"
docker run --rm "$DERIVED_TAG" >"$derived_output" 2>&1
grep -F "onestep-worker: target=/workspace/worker.yaml" "$derived_output"
grep -F '"marker": "mounted-pyproject"' "$derived_output"
```

- [ ] **Step 4: Run the smoke harness to verify it passes**

Run:

```bash
./scripts/run-worker-image-smoke.sh
```

Expected:

- the derived image builds successfully from `FROM onestep-worker-local:test`
- the derived image starts without extra run flags and prints `mounted-pyproject`

- [ ] **Step 5: Commit**

```bash
git add scripts/run-worker-image-smoke.sh tests/assets/worker_image/derived/Dockerfile
git commit -m "test: cover derived worker images"
```

### Task 4: Add CI smoke coverage and GHCR release publishing

**Files:**
- Modify: `.github/workflows/test.yml:3-77`
- Modify: `.github/workflows/release.yml:3-92`

- [ ] **Step 1: Write the failing workflow assertions**

Create a local workflow shape check and keep it in your shell history while editing:

```bash
uv run python - <<'PY'
import yaml
from pathlib import Path

ci = yaml.safe_load(Path(".github/workflows/test.yml").read_text())
release = yaml.safe_load(Path(".github/workflows/release.yml").read_text())

assert "docker/**" in ci["on"]["push"]["paths"]
assert "docker/**" in ci["on"]["pull_request"]["paths"]
assert "worker-image" in ci["jobs"]
assert "worker-image" in release["jobs"]
assert release["permissions"]["packages"] == "write"
PY
```

- [ ] **Step 2: Run the workflow assertions to verify they fail**

Run:

```bash
uv run python - <<'PY'
import yaml
from pathlib import Path

ci = yaml.safe_load(Path(".github/workflows/test.yml").read_text())
release = yaml.safe_load(Path(".github/workflows/release.yml").read_text())

assert "docker/**" in ci["on"]["push"]["paths"]
assert "docker/**" in ci["on"]["pull_request"]["paths"]
assert "worker-image" in ci["jobs"]
assert "worker-image" in release["jobs"]
assert release["permissions"]["packages"] == "write"
PY
```

Expected:

- the assertions fail because the CI workflow does not watch `docker/**`
- the assertions fail because no worker-image jobs exist yet
- the assertions fail because release permissions do not include `packages: write`

- [ ] **Step 3: Write the minimal CI and release workflow implementation**

Update `.github/workflows/test.yml` so the relevant sections are:

```yaml
on:
  push:
    paths:
      - ".github/workflows/test.yml"
      - ".github/workflows/release.yml"
      - "docker/**"
      - "docker-compose.integration.yml"
      - "pyproject.toml"
      - "scripts/**"
      - "src/**"
      - "tests/**"
      - "uv.lock"
  pull_request:
    paths:
      - ".github/workflows/test.yml"
      - ".github/workflows/release.yml"
      - "docker/**"
      - "docker-compose.integration.yml"
      - "pyproject.toml"
      - "scripts/**"
      - "src/**"
      - "tests/**"
      - "uv.lock"
```

Add a new job to `.github/workflows/test.yml`:

```yaml
  worker-image:
    name: Worker image smoke
    runs-on: ubuntu-latest
    needs: unit

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Build and smoke test worker image
        run: ./scripts/run-worker-image-smoke.sh
```

Change the integration job dependency in `.github/workflows/test.yml` to:

```yaml
  integration:
    name: Integration (py3.11)
    runs-on: ubuntu-latest
    needs:
      - unit
      - worker-image
    timeout-minutes: 20
```

Update `.github/workflows/release.yml` permissions:

```yaml
permissions:
  contents: write
  packages: write
```

Add a release smoke step to the existing `build` job:

```yaml
      - name: Smoke test worker image
        run: ./scripts/run-worker-image-smoke.sh
```

Add a new publish job to `.github/workflows/release.yml`:

```yaml
  worker-image:
    name: Publish worker image
    runs-on: ubuntu-latest
    needs: build
    if: github.event_name == 'push'
    permissions:
      contents: read
      packages: write

    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Set up Docker Buildx
        uses: docker/setup-buildx-action@v3

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Derive image tags
        run: |
          VERSION="${GITHUB_REF_NAME#v}"
          MAJOR_MINOR="${VERSION%.*}"
          IMAGE_NAME="ghcr.io/${GITHUB_REPOSITORY_OWNER,,}/onestep-worker"
          {
            echo "VERSION=$VERSION"
            echo "MAJOR_MINOR=$MAJOR_MINOR"
            echo "IMAGE_NAME=$IMAGE_NAME"
          } >> "$GITHUB_ENV"

      - name: Build and push worker image
        uses: docker/build-push-action@v6
        with:
          context: .
          file: docker/worker/Dockerfile
          platforms: linux/amd64,linux/arm64
          push: true
          tags: |
            ${{ env.IMAGE_NAME }}:${{ env.VERSION }}
            ${{ env.IMAGE_NAME }}:${{ env.MAJOR_MINOR }}
            ${{ env.IMAGE_NAME }}:latest
```

- [ ] **Step 4: Run the workflow assertions and smoke script to verify they pass**

Run:

```bash
uv run python - <<'PY'
import yaml
from pathlib import Path

ci = yaml.safe_load(Path(".github/workflows/test.yml").read_text())
release = yaml.safe_load(Path(".github/workflows/release.yml").read_text())

assert "docker/**" in ci["on"]["push"]["paths"]
assert "docker/**" in ci["on"]["pull_request"]["paths"]
assert "worker-image" in ci["jobs"]
assert "worker-image" in release["jobs"]
assert release["permissions"]["packages"] == "write"
PY
./scripts/run-worker-image-smoke.sh
```

Expected:

- the YAML assertions pass
- the smoke script still passes after workflow changes

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/test.yml .github/workflows/release.yml
git commit -m "ci: publish worker image to ghcr"
```

### Task 5: Document A/B usage, target contract, and troubleshooting

**Files:**
- Modify: `README.md:56-180`
- Create: `deploy/worker-runtime-image.md`
- Modify: `deploy/README.md:1-40`

- [ ] **Step 1: Write the failing documentation checks**

Use these grep checks as the doc acceptance gate:

```bash
rg -n "Official worker image|ONESTEP_TARGET|ghcr.io/.+/onestep-worker" README.md deploy/README.md deploy/worker-runtime-image.md
rg -n "requirements.txt|pyproject.toml|docker run --rm|FROM ghcr.io" deploy/worker-runtime-image.md
```

- [ ] **Step 2: Run the documentation checks to verify they fail**

Run:

```bash
rg -n "Official worker image|ONESTEP_TARGET|ghcr.io/.+/onestep-worker" README.md deploy/README.md deploy/worker-runtime-image.md
```

Expected:

- `deploy/worker-runtime-image.md` does not exist yet
- README does not yet mention the official worker image or `ONESTEP_TARGET`

- [ ] **Step 3: Write the minimal user-facing documentation**

Add this section to `README.md` after the YAML CLI examples:

````md
## Official worker image

`onestep` also ships an official worker runtime image for YAML-oriented workers.

Mounted workspace usage:

```bash
docker run --rm \
  -e ONESTEP_TARGET=/workspace/worker.yaml \
  -v "$PWD:/workspace" \
  ghcr.io/repository-owner/onestep-worker:1.2.62
```

Derived image usage:

```dockerfile
FROM ghcr.io/repository-owner/onestep-worker:1.2.62

WORKDIR /workspace
COPY . /workspace
ENV ONESTEP_TARGET=/workspace/worker.yaml
```

The runtime automatically adds `/workspace` and `/workspace/src` to `PYTHONPATH`.
If `/workspace/requirements.txt` exists it is installed first; otherwise the runtime
falls back to installing `/workspace` when `/workspace/pyproject.toml` exists.

See `deploy/worker-runtime-image.md` for the full usage guide and troubleshooting notes.
````

Create `deploy/worker-runtime-image.md`:

````md
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
- `installing requirements` fails: verify `requirements.txt` contents inside `/workspace`
- `onestep check` fails: run the same target locally to inspect YAML or import errors
````

Add this bullet to `deploy/README.md` under the included files list:

```md
- `worker-runtime-image.md`: official container runtime guide for mounted and derived worker images
```

- [ ] **Step 4: Run the documentation checks to verify they pass**

Run:

```bash
rg -n "Official worker image|ONESTEP_TARGET|ghcr.io/.+/onestep-worker" README.md deploy/README.md deploy/worker-runtime-image.md
rg -n "requirements.txt|pyproject.toml|docker run --rm|FROM ghcr.io" deploy/worker-runtime-image.md
```

Expected:

- all three docs contain the new worker image guidance
- the deployment guide includes both Mode A and Mode B examples plus troubleshooting

- [ ] **Step 5: Commit**

```bash
git add README.md deploy/README.md deploy/worker-runtime-image.md
git commit -m "docs: add worker image usage guide"
```

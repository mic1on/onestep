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

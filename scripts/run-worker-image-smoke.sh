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

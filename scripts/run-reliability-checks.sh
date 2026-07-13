#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ONESTEP_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${ONESTEP_PYTHON_BIN:-python3}"
fi

cd "$ROOT_DIR"

echo "==> Running core non-integration tests"
"$PYTHON_BIN" -m pytest -q -m "not integration" tests "$@"

plugin_paths=(
  "plugins/onestep-feishu-bitable/tests"
  "plugins/onestep-mysql/tests"
  "plugins/onestep-postgres/tests"
  "plugins/onestep-rabbitmq/tests"
  "plugins/onestep-redis/tests"
  "plugins/onestep-sqs/tests"
)

echo "==> Running plugin non-integration tests in isolated pytest processes"
for path in "${plugin_paths[@]}"; do
  if find "$ROOT_DIR/$path" -maxdepth 1 -type f -name 'test_*.py' | grep -q .; then
    echo "==> $path"
    "$PYTHON_BIN" -m pytest -q -m "not integration" "$path" "$@"
  else
    echo "==> $path (no tests found, skipped)"
  fi
done

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

# "<import name> <tests path>" per plugin
plugin_tests=(
  "onestep_clickhouse plugins/onestep-clickhouse/tests"
  "onestep_control_plane plugins/onestep-control-plane/tests"
  "onestep_elasticsearch plugins/onestep-elasticsearch/tests"
  "onestep_feishu_bitable plugins/onestep-feishu-bitable/tests"
  "onestep_mongodb plugins/onestep-mongodb/tests"
  "onestep_mysql plugins/onestep-mysql/tests"
  "onestep_postgres plugins/onestep-postgres/tests"
  "onestep_rabbitmq plugins/onestep-rabbitmq/tests"
  "onestep_redis plugins/onestep-redis/tests"
  "onestep_sqs plugins/onestep-sqs/tests"
  "onestep_cf_queues plugins/onestep-cf-queues/tests"
)

echo "==> Running plugin non-integration tests in isolated pytest processes"
for entry in "${plugin_tests[@]}"; do
  package="${entry%% *}"
  path="${entry#* }"
  if ! find "$ROOT_DIR/$path" -maxdepth 1 -type f -name 'test_*.py' | grep -q .; then
    echo "==> $path (no tests found, skipped)"
    continue
  fi
  # A partially-synced .venv must not abort the whole run with collection
  # errors; CI installs every plugin via `uv sync --all-packages` so nothing
  # is skipped there.
  if ! "$PYTHON_BIN" -c "import $package" >/dev/null 2>&1; then
    echo "==> $path (skipped: $package not importable; run 'uv sync' to install plugin deps)"
    continue
  fi
  echo "==> $path"
  "$PYTHON_BIN" -m pytest -q -m "not integration" "$path" "$@"
done

if "$PYTHON_BIN" - <<'PY'
import sys

raise SystemExit(0 if sys.version_info >= (3, 10) else 1)
PY
then
  if command -v uv >/dev/null 2>&1; then
    echo "==> plugins/onestep-kafka/tests"
    uv run --extra test --extra kafka python -m pytest -q -m "not integration" "$ROOT_DIR/plugins/onestep-kafka/tests" "$@"
  else
    echo "==> plugins/onestep-kafka/tests (uv not found, skipped)"
  fi
else
  echo "==> plugins/onestep-kafka/tests (requires Python >=3.10, skipped)"
fi

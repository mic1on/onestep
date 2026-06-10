#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.integration.yml"
KEEP_SERVICES="${KEEP_INTEGRATION_SERVICES:-0}"
PYTHON_BIN="${ONESTEP_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${ONESTEP_PYTHON_BIN:-python3}"
fi

cleanup() {
  local exit_code="$1"
  if [[ "$KEEP_SERVICES" != "1" ]]; then
    docker compose -f "$COMPOSE_FILE" down --remove-orphans >/dev/null 2>&1 || true
  fi
  exit "$exit_code"
}

trap 'cleanup $?' EXIT

docker compose -f "$COMPOSE_FILE" up -d

# shellcheck disable=SC1091
eval "$("$ROOT_DIR/scripts/setup-integration-env.sh")"

test_paths=()
for path in \
  tests/integration \
  plugins/onestep-rabbitmq/tests/integration \
  plugins/onestep-redis/tests/integration \
  plugins/onestep-mysql/tests/integration \
  plugins/onestep-sqs/tests/integration
do
  if find "$ROOT_DIR/$path" -maxdepth 1 -type f -name 'test_*.py' | grep -q .; then
    test_paths+=("$path")
  fi
done

"$PYTHON_BIN" -m pytest "${test_paths[@]}" -q "$@"

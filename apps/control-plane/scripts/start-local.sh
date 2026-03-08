#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
HOST="${ONESTEP_CP_HOST:-127.0.0.1}"
PORT="${ONESTEP_CP_PORT:-8080}"
DB_PATH="${ONESTEP_CP_SQLITE_PATH:-$ROOT_DIR/.data/control-plane-dev.db}"
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"

mkdir -p "$(dirname "$DB_PATH")"

export ONESTEP_CP_DATABASE_URL="${ONESTEP_CP_DATABASE_URL:-sqlite:///$DB_PATH}"
export ONESTEP_CP_INGEST_TOKENS="${ONESTEP_CP_INGEST_TOKENS:-dev-token}"

run_uv_tool() {
  if [ -x "$VENV_PYTHON" ]; then
    PYTHONPATH="${ROOT_DIR}/apps/api/src${PYTHONPATH:+:$PYTHONPATH}" \
      "$VENV_PYTHON" -m "$@"
    return
  fi
  if python3 -m uv --help >/dev/null 2>&1; then
    python3 -m uv run "$@"
    return
  fi
  echo "error: no usable Python runner found. Expected $VENV_PYTHON or python3 -m uv." >&2
  exit 1
}

echo "OneStep Control Plane"
echo "  root:   $ROOT_DIR"
echo "  host:   $HOST"
echo "  port:   $PORT"
echo "  db:     $ONESTEP_CP_DATABASE_URL"
echo "  tokens: $ONESTEP_CP_INGEST_TOKENS"
echo
echo "OpenAPI:  http://$HOST:$PORT/openapi.json"
echo "Services: http://$HOST:$PORT/api/v1/services?environment=dev"
echo

cd "$ROOT_DIR"
run_uv_tool alembic -c "$ROOT_DIR/alembic.ini" upgrade head
if [ -x "$VENV_PYTHON" ]; then
  exec env PYTHONPATH="${ROOT_DIR}/apps/api/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$VENV_PYTHON" -m uvicorn onestep_control_plane_api.main:app \
    --app-dir "$ROOT_DIR/apps/api/src" \
    --host "$HOST" \
    --port "$PORT"
fi
exec python3 -m uv run uvicorn onestep_control_plane_api.main:app \
  --app-dir "$ROOT_DIR/apps/api/src" \
  --host "$HOST" \
  --port "$PORT"

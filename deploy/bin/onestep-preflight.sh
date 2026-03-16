#!/bin/sh
set -eu

: "${APP_CWD:?APP_CWD is required}"
: "${APP_TARGET:?APP_TARGET is required}"
: "${ONESTEP_BIN:?ONESTEP_BIN is required}"

if [ ! -d "$APP_CWD" ]; then
  echo "onestep-preflight: APP_CWD does not exist: $APP_CWD" >&2
  exit 1
fi

if [ -x "$ONESTEP_BIN" ]; then
  RESOLVED_ONESTEP_BIN="$ONESTEP_BIN"
else
  RESOLVED_ONESTEP_BIN="$(command -v "$ONESTEP_BIN" 2>/dev/null || true)"
fi

if [ -z "${RESOLVED_ONESTEP_BIN:-}" ]; then
  echo "onestep-preflight: ONESTEP_BIN not found: $ONESTEP_BIN" >&2
  exit 1
fi

if [ -n "${PYTHONPATH:-}" ]; then
  export PYTHONPATH="$APP_CWD:$PYTHONPATH"
else
  export PYTHONPATH="$APP_CWD"
fi

cd "$APP_CWD"
echo "onestep-preflight: checking $APP_TARGET"
exec "$RESOLVED_ONESTEP_BIN" check "$APP_TARGET"

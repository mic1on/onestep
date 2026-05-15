#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=""
SMOKE_BUILD="${SMOKE_BUILD:-0}"
SMOKE_MANAGE_STACK="${SMOKE_MANAGE_STACK:-0}"
SMOKE_CLEANUP="${SMOKE_CLEANUP:-0}"

usage() {
  cat <<'EOF'
Usage: bash scripts/run-smoke.sh [--compose-file FILE] [--env-file FILE]
EOF
}

resolve_path() {
  case "$1" in
    /*) printf '%s\n' "$1" ;;
    *) printf '%s/%s\n' "$ROOT_DIR" "$1" ;;
  esac
}

while [ $# -gt 0 ]; do
  case "$1" in
    --compose-file)
      COMPOSE_FILE=$2
      shift 2
      ;;
    --env-file)
      ENV_FILE=$2
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

COMPOSE_PATH=$(resolve_path "$COMPOSE_FILE")
[ -f "$COMPOSE_PATH" ] || {
  echo "error: compose file not found: $COMPOSE_FILE" >&2
  exit 1
}

ENV_ARG=""
if [ -n "$ENV_FILE" ]; then
  ENV_PATH=$(resolve_path "$ENV_FILE")
  [ -f "$ENV_PATH" ] || {
    echo "error: env file not found: $ENV_FILE" >&2
    exit 1
  }
  set -a
  # shellcheck disable=SC1090
  . "$ENV_PATH"
  set +a
  ENV_ARG="--env-file $ENV_PATH"
fi

READY_TIMEOUT_S="${ONESTEP_CP_SMOKE_READY_TIMEOUT_S:-120}"
API_URL="${ONESTEP_CP_SMOKE_API_URL:-http://127.0.0.1:${ONESTEP_CP_API_PORT:-8000}}"
FRONTEND_URL="${ONESTEP_CP_SMOKE_FRONTEND_URL:-http://127.0.0.1:${ONESTEP_CP_FRONTEND_PORT:-4173}}"

compose() {
  docker compose ${ENV_ARG:+$ENV_ARG }-f "$COMPOSE_PATH" "$@"
}

cleanup() {
  if [ "$SMOKE_CLEANUP" = "1" ] || [ "$SMOKE_CLEANUP" = "true" ]; then
    compose down -v --remove-orphans
  fi
}

wait_for_url() {
  url=$1
  label=$2
  deadline=$(( $(date +%s) + READY_TIMEOUT_S ))
  while [ "$(date +%s)" -lt "$deadline" ]; do
    if curl -fsS "$url" >/tmp/onestep-smoke-response.$$ 2>/dev/null; then
      cat /tmp/onestep-smoke-response.$$
      rm -f /tmp/onestep-smoke-response.$$
      return 0
    fi
    sleep 2
  done
  rm -f /tmp/onestep-smoke-response.$$
  echo "error: timed out waiting for $label at $url" >&2
  return 1
}

trap cleanup EXIT INT TERM

if [ "$SMOKE_MANAGE_STACK" = "1" ] || [ "$SMOKE_MANAGE_STACK" = "true" ]; then
  if [ "$SMOKE_BUILD" = "1" ] || [ "$SMOKE_BUILD" = "true" ]; then
    compose build api frontend
  fi
  compose up -d postgres
  compose run --rm migrate
  compose up -d api frontend
fi

compose ps

health_payload=$(wait_for_url "$API_URL/healthz" "api health")
ready_payload=$(wait_for_url "$API_URL/readyz" "api readiness")
frontend_payload=$(wait_for_url "$FRONTEND_URL/" "frontend index")
config_payload=$(wait_for_url "$FRONTEND_URL/app-config.js" "frontend runtime config")

printf '%s\n' "$health_payload" | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'
printf '%s\n' "$ready_payload" | grep -q '"status"[[:space:]]*:[[:space:]]*"ready"'
printf '%s\n' "$frontend_payload" | grep -qi '<html'
printf '%s\n' "$config_payload" | grep -q 'window.__APP_CONFIG__'

printf 'smoke ok\n'
printf '  api: %s\n' "$API_URL"
printf '  frontend: %s\n' "$FRONTEND_URL"

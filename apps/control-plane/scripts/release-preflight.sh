#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
COMPOSE_FILE="docker-compose.yml"
ENV_FILE=""

usage() {
  cat <<'EOF'
Usage: bash scripts/release-preflight.sh [--compose-file FILE] [--env-file FILE]
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

command -v docker >/dev/null 2>&1 || {
  echo "error: docker is required" >&2
  exit 1
}

docker compose version >/dev/null 2>&1 || {
  echo "error: docker compose plugin is required" >&2
  exit 1
}

docker compose ${ENV_ARG:+$ENV_ARG }-f "$COMPOSE_PATH" config >/dev/null

if [ "$(basename "$COMPOSE_PATH")" = "docker-compose.deploy.yml" ]; then
  : "${ONESTEP_CP_API_IMAGE:?set ONESTEP_CP_API_IMAGE in the deploy env file}"
  : "${ONESTEP_CP_FRONTEND_IMAGE:?set ONESTEP_CP_FRONTEND_IMAGE in the deploy env file}"
  : "${ONESTEP_CP_INGEST_TOKENS:?set ONESTEP_CP_INGEST_TOKENS in the deploy env file}"
fi

if [ -z "${ONESTEP_CP_DATABASE_URL:-}" ]; then
  : "${ONESTEP_CP_POSTGRES_PASSWORD:?set ONESTEP_CP_POSTGRES_PASSWORD when using the bundled postgres service}"
fi

printf 'preflight ok\n'
printf '  compose: %s\n' "$COMPOSE_PATH"
if [ -n "$ENV_FILE" ]; then
  printf '  env: %s\n' "$ENV_PATH"
else
  printf '  env: none\n'
fi

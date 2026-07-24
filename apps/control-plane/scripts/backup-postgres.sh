#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/backup-postgres.sh [--env-file PATH] [--database-url URL] [--output PATH]

Create a PostgreSQL backup in pg_dump custom format.

Options:
  --env-file PATH      Load environment variables from this file before resolving defaults.
  --database-url URL   Explicit PostgreSQL connection string. Overrides env-derived values.
  --output PATH        Backup file path. Default: backups/onestep-control-plane-<timestamp>.dump
  --help               Show this message.
EOF
}

ENV_FILE=""
DATABASE_URL="${ONESTEP_CP_DATABASE_URL:-}"
OUTPUT_PATH=""

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      ENV_FILE="${2:?missing value for --env-file}"
      shift 2
      ;;
    --database-url)
      DATABASE_URL="${2:?missing value for --database-url}"
      shift 2
      ;;
    --output)
      OUTPUT_PATH="${2:?missing value for --output}"
      shift 2
      ;;
    --help)
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

if [ -n "$ENV_FILE" ]; then
  if [ ! -f "$ENV_FILE" ]; then
    echo "error: env file not found: $ENV_FILE" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
  if [ -z "$DATABASE_URL" ]; then
    DATABASE_URL="${ONESTEP_CP_DATABASE_URL:-}"
  fi
fi

if ! command -v pg_dump >/dev/null 2>&1; then
  echo "error: pg_dump is not installed or not on PATH" >&2
  exit 1
fi

if [ -z "$DATABASE_URL" ]; then
  POSTGRES_DB="${ONESTEP_CP_POSTGRES_DB:-onestep_control_plane}"
  POSTGRES_USER="${ONESTEP_CP_POSTGRES_USER:-postgres}"
  POSTGRES_PASSWORD="${ONESTEP_CP_POSTGRES_PASSWORD:-}"
  POSTGRES_PORT="${ONESTEP_CP_POSTGRES_PORT:-5432}"
  if [ -z "$POSTGRES_PASSWORD" ]; then
    echo "error: set ONESTEP_CP_DATABASE_URL or ONESTEP_CP_POSTGRES_PASSWORD" >&2
    exit 1
  fi
  DATABASE_URL="postgresql://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}"
fi

DATABASE_URL="${DATABASE_URL/postgresql+psycopg:\/\//postgresql://}"

if [ -z "$OUTPUT_PATH" ]; then
  OUTPUT_PATH="backups/onestep-control-plane-$(date -u +%Y%m%dT%H%M%SZ).dump"
fi

mkdir -p "$(dirname "$OUTPUT_PATH")"

echo "Creating PostgreSQL backup at $OUTPUT_PATH"
pg_dump \
  --format=custom \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --file "$OUTPUT_PATH" \
  "$DATABASE_URL"

echo "Backup complete: $OUTPUT_PATH"

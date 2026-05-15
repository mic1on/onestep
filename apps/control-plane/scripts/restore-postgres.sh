#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/restore-postgres.sh --input PATH [--env-file PATH] [--database-url URL] [--yes]

Restore a PostgreSQL database from a pg_dump custom-format backup.

Options:
  --input PATH         Required backup file path produced by backup-postgres.sh.
  --env-file PATH      Load environment variables from this file before resolving defaults.
  --database-url URL   Explicit PostgreSQL connection string. Overrides env-derived values.
  --yes                Confirm that the target database can be overwritten.
  --help               Show this message.
EOF
}

ENV_FILE=""
DATABASE_URL="${ONESTEP_CP_DATABASE_URL:-}"
INPUT_PATH=""
CONFIRMED="false"

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
    --input)
      INPUT_PATH="${2:?missing value for --input}"
      shift 2
      ;;
    --yes)
      CONFIRMED="true"
      shift
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

if [ "$CONFIRMED" != "true" ]; then
  echo "error: restore requires --yes" >&2
  exit 1
fi

if [ -z "$INPUT_PATH" ]; then
  echo "error: --input is required" >&2
  exit 1
fi

if [ ! -f "$INPUT_PATH" ]; then
  echo "error: backup file not found: $INPUT_PATH" >&2
  exit 1
fi

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

if ! command -v pg_restore >/dev/null 2>&1; then
  echo "error: pg_restore is not installed or not on PATH" >&2
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

echo "Restoring PostgreSQL backup from $INPUT_PATH"
pg_restore \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --single-transaction \
  --exit-on-error \
  --dbname "$DATABASE_URL" \
  "$INPUT_PATH"

echo "Restore complete from $INPUT_PATH"

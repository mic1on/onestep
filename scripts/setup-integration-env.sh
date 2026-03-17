#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/docker-compose.integration.yml"
LOCALSTACK_ENDPOINT="${LOCALSTACK_ENDPOINT:-http://127.0.0.1:4566}"
RABBITMQ_URL="${ONESTEP_RABBITMQ_URL:-amqp://guest:guest@127.0.0.1:5672/}"
RABBITMQ_QUEUE="${ONESTEP_RABBITMQ_QUEUE:-onestep.integration}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379}"
AWS_REGION_VALUE="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
SQS_QUEUE_NAME="${ONESTEP_SQS_QUEUE_NAME:-onestep-integration.fifo}"
SQS_GROUP_ID_VALUE="${ONESTEP_SQS_GROUP_ID:-workers}"
MYSQL_HOST="${ONESTEP_MYSQL_HOST:-127.0.0.1}"
MYSQL_PORT="${ONESTEP_MYSQL_PORT:-3306}"
MYSQL_DATABASE="${ONESTEP_MYSQL_DATABASE:-onestep}"
MYSQL_USER="${ONESTEP_MYSQL_USER:-root}"
MYSQL_PASSWORD="${ONESTEP_MYSQL_PASSWORD:-root}"
PYTHON_BIN="${ONESTEP_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${ONESTEP_PYTHON_BIN:-python3}"
fi

wait_for_url() {
  local url="$1"
  local label="$2"
  local attempts="${3:-60}"
  local sleep_s="${4:-2}"

  for _ in $(seq 1 "$attempts"); do
    if curl -fsS "$url" >/dev/null 2>&1; then
      return 0
    fi
    sleep "$sleep_s"
  done

  echo "Timed out waiting for $label at $url" >&2
  return 1
}

wait_for_rabbitmq() {
  local attempts="${1:-60}"
  for _ in $(seq 1 "$attempts"); do
    if curl -fsS -u guest:guest http://127.0.0.1:15672/api/overview >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  echo "Timed out waiting for RabbitMQ management API" >&2
  return 1
}

wait_for_mysql() {
  local attempts="${1:-60}"
  MYSQL_HOST="$MYSQL_HOST" \
  MYSQL_PORT="$MYSQL_PORT" \
  MYSQL_USER="$MYSQL_USER" \
  MYSQL_PASSWORD="$MYSQL_PASSWORD" \
  MYSQL_DATABASE="$MYSQL_DATABASE" \
  "$PYTHON_BIN" - <<'PY'
import os
import time

import pymysql

host = os.environ["MYSQL_HOST"]
port = int(os.environ["MYSQL_PORT"])
user = os.environ["MYSQL_USER"]
password = os.environ["MYSQL_PASSWORD"]
database = os.environ["MYSQL_DATABASE"]

last_error = None
for _ in range(60):
    try:
        conn = pymysql.connect(
            host=host,
            port=port,
            user=user,
            password=password,
            database=database,
            connect_timeout=2,
            autocommit=True,
        )
        conn.close()
        raise SystemExit(0)
    except Exception as exc:  # pragma: no cover - shell retry path
        last_error = exc
        time.sleep(2)
raise SystemExit(f"Timed out waiting for MySQL: {last_error}")
PY
}

wait_for_redis() {
  REDIS_URL="$REDIS_URL" "$PYTHON_BIN" - <<'PY'
import os
import time

import redis

url = os.environ["REDIS_URL"]
last_error = None
for _ in range(60):
    try:
        client = redis.Redis.from_url(url)
        client.ping()
        client.close()
        raise SystemExit(0)
    except Exception as exc:  # pragma: no cover - shell retry path
        last_error = exc
        time.sleep(2)
raise SystemExit(f"Timed out waiting for Redis: {last_error}")
PY
}

ensure_sqs_queue() {
  "$PYTHON_BIN" - <<'PY'
import boto3
import json
import os

endpoint = os.environ["LOCALSTACK_ENDPOINT"]
region = os.environ["AWS_REGION_VALUE"]
queue_name = os.environ["SQS_QUEUE_NAME"]
client = boto3.client(
    "sqs",
    endpoint_url=endpoint,
    region_name=region,
    aws_access_key_id="test",
    aws_secret_access_key="test",
)
attributes = {}
if queue_name.endswith(".fifo"):
    attributes["FifoQueue"] = "true"
    attributes["ContentBasedDeduplication"] = "true"
result = client.create_queue(QueueName=queue_name, Attributes=attributes)
print(json.dumps({"QueueUrl": result["QueueUrl"]}))
PY
}

if [[ "${1:-}" == "--start" ]]; then
  docker compose -f "$COMPOSE_FILE" up -d
fi

wait_for_url "$LOCALSTACK_ENDPOINT/_localstack/health" "LocalStack"
wait_for_rabbitmq
wait_for_mysql
wait_for_redis

SQS_QUEUE_JSON="$(LOCALSTACK_ENDPOINT="$LOCALSTACK_ENDPOINT" AWS_REGION_VALUE="$AWS_REGION_VALUE" SQS_QUEUE_NAME="$SQS_QUEUE_NAME" ensure_sqs_queue)"
SQS_QUEUE_URL="$(printf '%s' "$SQS_QUEUE_JSON" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin)["QueueUrl"])')"
MYSQL_DSN="mysql+pymysql://$MYSQL_USER:$MYSQL_PASSWORD@$MYSQL_HOST:$MYSQL_PORT/$MYSQL_DATABASE"

cat <<ENV
export PYTHONPATH="$ROOT_DIR/src"
export AWS_REGION="$AWS_REGION_VALUE"
export AWS_DEFAULT_REGION="$AWS_REGION_VALUE"
export AWS_ACCESS_KEY_ID="test"
export AWS_SECRET_ACCESS_KEY="test"
export AWS_ENDPOINT_URL="$LOCALSTACK_ENDPOINT"
export ONESTEP_RABBITMQ_URL="$RABBITMQ_URL"
export ONESTEP_RABBITMQ_QUEUE="$RABBITMQ_QUEUE"
export REDIS_URL="$REDIS_URL"
export ONESTEP_SQS_QUEUE_URL="$SQS_QUEUE_URL"
export ONESTEP_SQS_GROUP_ID="$SQS_GROUP_ID_VALUE"
export ONESTEP_MYSQL_DSN="$MYSQL_DSN"
export ONESTEP_MYSQL_HOST="$MYSQL_HOST"
export ONESTEP_MYSQL_PORT="$MYSQL_PORT"
export ONESTEP_MYSQL_DATABASE="$MYSQL_DATABASE"
export ONESTEP_MYSQL_USER="$MYSQL_USER"
ENV

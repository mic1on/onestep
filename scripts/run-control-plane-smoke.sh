#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
CONTROL_PLANE_DIR="${ONESTEP_CONTROL_PLANE_DIR:-$ROOT_DIR/../onestep-control-plane}"
CONTROL_PLANE_URL="${ONESTEP_CONTROL_PLANE_URL:-http://127.0.0.1:8080}"
CONTROL_PLANE_TOKEN="${ONESTEP_CONTROL_PLANE_TOKEN:-dev-token}"
SMOKE_ENVIRONMENT="${ONESTEP_CONTROL_PLANE_ENVIRONMENT:-dev}"
STARTUP_TIMEOUT_S="${ONESTEP_CONTROL_PLANE_SMOKE_TIMEOUT_S:-45}"
POLL_INTERVAL_S="${ONESTEP_CONTROL_PLANE_SMOKE_POLL_S:-1}"

if [ ! -d "$CONTROL_PLANE_DIR" ]; then
  echo "error: control plane repo not found at $CONTROL_PLANE_DIR" >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "error: curl is required" >&2
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "error: python3 is required" >&2
  exit 1
fi

case "$CONTROL_PLANE_URL" in
  http://127.0.0.1:*|http://localhost:*|ws://127.0.0.1:*|ws://localhost:*)
    unset HTTP_PROXY HTTPS_PROXY ALL_PROXY
    export NO_PROXY=127.0.0.1,localhost
    ;;
esac

CP_LOG=$(mktemp -t onestep-control-plane-smoke-cp.XXXXXX.log)
AGENT_LOG=$(mktemp -t onestep-control-plane-smoke-agent.XXXXXX.log)
CP_PID=""
AGENT_PID=""

cleanup() {
  if [ -n "$AGENT_PID" ] && kill -0 "$AGENT_PID" 2>/dev/null; then
    kill "$AGENT_PID" 2>/dev/null || true
    wait "$AGENT_PID" 2>/dev/null || true
  fi
  if [ -n "$CP_PID" ] && kill -0 "$CP_PID" 2>/dev/null; then
    kill "$CP_PID" 2>/dev/null || true
    wait "$CP_PID" 2>/dev/null || true
  fi
}

print_logs_and_fail() {
  echo
  echo "control-plane log: $CP_LOG" >&2
  tail -n 40 "$CP_LOG" >&2 || true
  echo
  echo "agent log: $AGENT_LOG" >&2
  tail -n 60 "$AGENT_LOG" >&2 || true
  exit 1
}

trap cleanup EXIT INT TERM

echo "Starting control plane from $CONTROL_PLANE_DIR"
(
  cd "$CONTROL_PLANE_DIR"
  ONESTEP_CP_HOST=127.0.0.1 \
    ONESTEP_CP_PORT="${CONTROL_PLANE_URL##*:}" \
    ONESTEP_CP_INGEST_TOKENS="$CONTROL_PLANE_TOKEN" \
    ./scripts/start-local.sh
) >"$CP_LOG" 2>&1 &
CP_PID=$!

READY_URL="$CONTROL_PLANE_URL/readyz"
deadline=$(( $(date +%s) + STARTUP_TIMEOUT_S ))
while :; do
  if curl --noproxy '*' -fsS "$READY_URL" >/dev/null 2>&1; then
    break
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "error: control plane did not become ready at $READY_URL" >&2
    print_logs_and_fail
  fi
  sleep "$POLL_INTERVAL_S"
done

echo "Starting OneStep reporter demo"
(
  cd "$ROOT_DIR"
  ONESTEP_CONTROL_PLANE_URL="$CONTROL_PLANE_URL" \
    ONESTEP_CONTROL_PLANE_TOKEN="$CONTROL_PLANE_TOKEN" \
    ONESTEP_CONTROL_PLANE_TIMEOUT_S=1 \
    CONTROL_PLANE_DEMO_INTERVAL_S=2 \
    CONTROL_PLANE_DEMO_WORKER_TIMEOUT_S=0.75 \
    CONTROL_PLANE_DEMO_SLOW_JOB_SLEEP_S=2.0 \
    ./scripts/run-control-plane-demo.sh
) >"$AGENT_LOG" 2>&1 &
AGENT_PID=$!

dashboard_url="$CONTROL_PLANE_URL/api/v1/services/control-plane-demo/dashboard?environment=$SMOKE_ENVIRONMENT"
sessions_url="$CONTROL_PLANE_URL/api/v1/services/control-plane-demo/sessions?environment=$SMOKE_ENVIRONMENT&status=active&limit=20"
dashboard_json=""
sessions_json=""
deadline=$(( $(date +%s) + STARTUP_TIMEOUT_S ))
while :; do
  dashboard_json=$(curl --noproxy '*' -fsS "$dashboard_url" 2>/dev/null || true)
  sessions_json=$(curl --noproxy '*' -fsS "$sessions_url" 2>/dev/null || true)
  if [ -n "$dashboard_json" ] && [ -n "$sessions_json" ]; then
    active_session_count=$(printf '%s' "$dashboard_json" | python3 -c 'import json,sys; payload=json.load(sys.stdin); print(payload["command_overview"]["active_session_count"])')
    active_instance_id=$(printf '%s' "$sessions_json" | python3 -c 'import json,sys; payload=json.load(sys.stdin); items=payload["items"]; print(items[0]["instance_id"] if items else "")')
    if [ "$active_session_count" -ge 1 ] && [ -n "$active_instance_id" ]; then
      break
    fi
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "error: demo service did not establish an active session" >&2
    print_logs_and_fail
  fi
  sleep "$POLL_INTERVAL_S"
done

instance_id="$active_instance_id"
if [ -z "$instance_id" ]; then
  echo "error: no active control-plane-demo instance found" >&2
  print_logs_and_fail
fi

create_command_url="$CONTROL_PLANE_URL/api/v1/instances/$instance_id/commands"
command_json=$(curl --noproxy '*' -fsS -X POST "$create_command_url" \
  -H 'Content-Type: application/json' \
  -d '{"kind":"ping","args":{"source":"smoke"},"timeout_s":10}')
command_id=$(printf '%s' "$command_json" | python3 -c 'import json,sys; payload=json.load(sys.stdin); print(payload["command_id"])')

commands_url="$CONTROL_PLANE_URL/api/v1/instances/$instance_id/commands?limit=20"
deadline=$(( $(date +%s) + STARTUP_TIMEOUT_S ))
while :; do
  commands_json=$(curl --noproxy '*' -fsS "$commands_url" 2>/dev/null || true)
  if [ -n "$commands_json" ]; then
    command_status=$(printf '%s' "$commands_json" | python3 -c 'import json,sys; payload=json.load(sys.stdin); command_id=sys.argv[1]; items={item["command_id"]: item for item in payload["items"]}; print(items.get(command_id, {}).get("status", ""))' "$command_id")
    if [ "$command_status" = "succeeded" ]; then
      command_duration=$(printf '%s' "$commands_json" | python3 -c 'import json,sys; payload=json.load(sys.stdin); command_id=sys.argv[1]; items={item["command_id"]: item for item in payload["items"]}; print(items.get(command_id, {}).get("duration_ms", ""))' "$command_id")
      echo "Smoke passed"
      echo "  service dashboard: $CONTROL_PLANE_URL/services/control-plane-demo?environment=$SMOKE_ENVIRONMENT"
      echo "  command id:        $command_id"
      echo "  command duration:  ${command_duration:-n/a}ms"
      exit 0
    fi
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "error: ping command did not succeed within timeout" >&2
    print_logs_and_fail
  fi
  sleep "$POLL_INTERVAL_S"
done

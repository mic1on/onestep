#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
VENV_PYTHON="$ROOT_DIR/.venv/bin/python"
WAIT_TIMEOUT="${ONESTEP_CONTROL_PLANE_WAIT_TIMEOUT_S:-15}"

export ONESTEP_CONTROL_PLANE_URL="${ONESTEP_CONTROL_PLANE_URL:-http://192.168.1.214:8000}"
export ONESTEP_CONTROL_PLANE_TOKEN="${ONESTEP_CONTROL_PLANE_TOKEN:-dev-token}"
export ONESTEP_ENV="${ONESTEP_ENV:-dev}"
export ONESTEP_SERVICE_NAME="${ONESTEP_SERVICE_NAME:-control-plane-demo}"
export ONESTEP_NODE_NAME="${ONESTEP_NODE_NAME:-$(hostname)}"
export ONESTEP_DEPLOYMENT_VERSION="${ONESTEP_DEPLOYMENT_VERSION:-demo}"
export ONESTEP_CONTROL_PLANE_HEARTBEAT_INTERVAL_S="${ONESTEP_CONTROL_PLANE_HEARTBEAT_INTERVAL_S:-10}"
export ONESTEP_CONTROL_PLANE_METRICS_INTERVAL_S="${ONESTEP_CONTROL_PLANE_METRICS_INTERVAL_S:-10}"
export ONESTEP_CONTROL_PLANE_EVENT_FLUSH_INTERVAL_S="${ONESTEP_CONTROL_PLANE_EVENT_FLUSH_INTERVAL_S:-1}"
export ONESTEP_CONTROL_PLANE_TIMEOUT_S="${ONESTEP_CONTROL_PLANE_TIMEOUT_S:-1}"
export CONTROL_PLANE_DEMO_INTERVAL_S="${CONTROL_PLANE_DEMO_INTERVAL_S:-3}"
export CONTROL_PLANE_DEMO_WORKER_TIMEOUT_S="${CONTROL_PLANE_DEMO_WORKER_TIMEOUT_S:-0.75}"
export CONTROL_PLANE_DEMO_SLOW_JOB_SLEEP_S="${CONTROL_PLANE_DEMO_SLOW_JOB_SLEEP_S:-2.0}"

case "$ONESTEP_CONTROL_PLANE_URL" in
  http://127.0.0.1:*|http://localhost:*|https://127.0.0.1:*|https://localhost:*)
    unset HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
    if [ -n "${NO_PROXY:-}" ]; then
      export NO_PROXY="$NO_PROXY,127.0.0.1,localhost"
    else
      export NO_PROXY="127.0.0.1,localhost"
    fi
    ;;
esac

echo "OneStep Control Plane Reporter Demo"
echo "  root:        $ROOT_DIR"
echo "  control-url: $ONESTEP_CONTROL_PLANE_URL"
echo "  token:       $ONESTEP_CONTROL_PLANE_TOKEN"
echo "  service:     $ONESTEP_SERVICE_NAME"
echo "  environment: $ONESTEP_ENV"
echo "  interval:    $CONTROL_PLANE_DEMO_INTERVAL_S"
echo "  timeout:     $CONTROL_PLANE_DEMO_WORKER_TIMEOUT_S"
echo "  slow-sleep:  $CONTROL_PLANE_DEMO_SLOW_JOB_SLEEP_S"
echo

cd "$ROOT_DIR"
WAIT_PYTHON="$VENV_PYTHON"
if [ ! -x "$WAIT_PYTHON" ]; then
  WAIT_PYTHON="python3"
fi
"$WAIT_PYTHON" - <<'PY'
import os
import sys
import time
from urllib import error, request
from urllib.parse import urlsplit, urlunsplit

raw_base_url = os.environ["ONESTEP_CONTROL_PLANE_URL"].rstrip("/")
timeout_s = float(os.environ.get("ONESTEP_CONTROL_PLANE_WAIT_TIMEOUT_S", "15"))
deadline = time.time() + timeout_s

parsed = urlsplit(raw_base_url)
scheme_map = {
    "http": "http",
    "https": "https",
    "ws": "http",
    "wss": "https",
}
if parsed.scheme not in scheme_map:
    print(f"error: unsupported control plane URL scheme: {parsed.scheme}", file=sys.stderr)
    sys.exit(1)

path = parsed.path.rstrip("/")
if path.endswith("/api/v1/agents/ws"):
    path = path[: -len("/api/v1/agents/ws")]

base_url = urlunsplit((scheme_map[parsed.scheme], parsed.netloc, path, "", ""))
ready_url = f"{base_url}/readyz"

while time.time() < deadline:
    try:
        with request.urlopen(ready_url, timeout=2.0) as response:
            if 200 <= response.status < 300:
                sys.exit(0)
    except (error.URLError, TimeoutError):
        time.sleep(0.5)

print(
    f"error: control plane did not become ready within {timeout_s:.1f}s at {ready_url}",
    file=sys.stderr,
)
sys.exit(1)
PY

if [ -x "$VENV_PYTHON" ]; then
  exec env PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
    "$VENV_PYTHON" "$ROOT_DIR/example/control_plane_reporter_demo.py"
fi
exec env PYTHONPATH="$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}" \
  python3 -m uv run python "$ROOT_DIR/example/control_plane_reporter_demo.py"

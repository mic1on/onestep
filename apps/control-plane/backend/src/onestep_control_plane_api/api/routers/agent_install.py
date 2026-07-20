from __future__ import annotations

import shlex

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["worker-agents"])


@router.get("/agent-install.sh", include_in_schema=False)
def agent_install_script(request: Request) -> PlainTextResponse:
    plane_url = _plane_url_from_request(request)
    script = _build_agent_install_script(plane_url)
    return PlainTextResponse(script, media_type="text/x-shellscript")


def _plane_url_from_request(request: Request) -> str:
    forwarded_proto = _first_header_value(request.headers.get("x-forwarded-proto"))
    forwarded_host = _first_header_value(request.headers.get("x-forwarded-host"))
    host = forwarded_host or _first_header_value(request.headers.get("host"))
    if host:
        proto = forwarded_proto or request.url.scheme
        prefix = (request.headers.get("x-forwarded-prefix") or "").rstrip("/")
        return f"{proto}://{host}{prefix}"
    return str(request.base_url).rstrip("/")


def _first_header_value(value: str | None) -> str | None:
    if value is None:
        return None
    first_value = value.split(",", maxsplit=1)[0].strip()
    return first_value or None


def _build_agent_install_script(plane_url: str) -> str:
    quoted_plane_url = shlex.quote(plane_url.rstrip("/"))
    return f"""#!/usr/bin/env bash
set -euo pipefail

PLANE_URL={quoted_plane_url}
TOKEN=""
NAME="$(hostname)"
MAX_CONCURRENCY="1"

usage() {{
  cat <<'USAGE'
Usage:
  curl -fsSL <plane-url>/agent-install.sh | bash -s -- \\
    --token <token> [--name <name>] [--max-concurrency <n>]

Options:
  --token <token>              Worker-agent registration token.
  --name <name>                Agent display name. Defaults to hostname.
  --max-concurrency <n>        Maximum concurrent deployments. Defaults to 1.
  --plane-url <url>            Override the Control Plane URL inferred by this script.
  -h, --help                   Show this help.
USAGE
}}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --token)
      TOKEN="${{2:-}}"
      shift 2
      ;;
    --name)
      NAME="${{2:-}}"
      shift 2
      ;;
    --max-concurrency)
      MAX_CONCURRENCY="${{2:-}}"
      shift 2
      ;;
    --plane-url)
      PLANE_URL="${{2:-}}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$TOKEN" ]]; then
  echo "Missing required --token." >&2
  usage >&2
  exit 2
fi

if [[ -z "$MAX_CONCURRENCY" || ! "$MAX_CONCURRENCY" =~ ^[0-9]+$ || "$MAX_CONCURRENCY" -lt 1 ]]; then
  echo "--max-concurrency must be a positive integer." >&2
  exit 2
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is required to install onestep-worker-agent." >&2
  exit 1
fi

python3 -m pip install --upgrade \\
  onestep-worker-agent \\
  'onestep[all]>=1.7.1' \\
  'onestep-feishu-bitable>=0.1.3'

if command -v onestep-agent >/dev/null 2>&1; then
  ONESTEP_AGENT=(onestep-agent)
else
  PYTHON_USER_BASE="$(python3 -m site --user-base 2>/dev/null || true)"
  if [[ -n "$PYTHON_USER_BASE" && -x "$PYTHON_USER_BASE/bin/onestep-agent" ]]; then
    ONESTEP_AGENT=("$PYTHON_USER_BASE/bin/onestep-agent")
  else
    echo "onestep-agent was installed, but its executable is not on PATH." >&2
    echo "Add the Python scripts directory to PATH and rerun this command." >&2
    exit 1
  fi
fi

"${{ONESTEP_AGENT[@]}}" setup \\
  --plane-url "$PLANE_URL" \\
  --registration-token "$TOKEN" \\
  --name "$NAME" \\
  --max-concurrency "$MAX_CONCURRENCY" \\
  --force \\
  --no-start

"${{ONESTEP_AGENT[@]}}" start
"""

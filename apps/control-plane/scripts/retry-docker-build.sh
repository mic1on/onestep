#!/usr/bin/env bash
#
# Run a docker build command, retrying transient registry failures.
#
# Registry rate limits (notably ECR Public's anonymous "toomanyrequests / Data
# limit exceeded") and plain network hiccups fail image builds for reasons that
# have nothing to do with the build itself. Wrap the build command with this
# script so one unlucky pull does not fail CI.
#
# Usage:
#   bash scripts/retry-docker-build.sh docker build --target plane -t img .
#   bash scripts/retry-docker-build.sh docker compose build plane
#
# Tunables (environment):
#   RETRY_DOCKER_MAX_ATTEMPTS   total attempts, default 3
#   RETRY_DOCKER_INITIAL_DELAY  seconds before the first retry, default 20
#   RETRY_DOCKER_MAX_DELAY      delay ceiling in seconds, default 180

set -uo pipefail

MAX_ATTEMPTS="${RETRY_DOCKER_MAX_ATTEMPTS:-3}"
INITIAL_DELAY="${RETRY_DOCKER_INITIAL_DELAY:-20}"
MAX_DELAY="${RETRY_DOCKER_MAX_DELAY:-180}"

if [ "$#" -eq 0 ]; then
  echo "error: no command given" >&2
  echo "usage: bash scripts/retry-docker-build.sh <command> [args...]" >&2
  exit 2
fi

# Keep this list to failures a later attempt can plausibly fix. Anything else
# (a broken Dockerfile, a missing context file) should fail immediately rather
# than burn three build attempts.
# A bare "429" is deliberately absent: build logs carry unrelated numbers, and a
# spurious retry costs three full build attempts.
TRANSIENT_PATTERN='toomanyrequests|too many requests|data limit exceeded|failed to resolve source metadata|unexpected status from get request|failed to copy|tls handshake timeout|i/o timeout|connection reset by peer|unexpected eof|502 bad gateway|503 service unavailable|temporary failure in name resolution|no such host'

log_file=$(mktemp "${TMPDIR:-/tmp}/retry-docker-build.XXXXXX")
cleanup() { rm -f "$log_file"; }
trap cleanup EXIT INT TERM

attempt=1
delay="$INITIAL_DELAY"

while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
  printf '[retry-docker-build] attempt %s/%s: %s\n' \
    "$attempt" "$MAX_ATTEMPTS" "$*" >&2

  # tee so the build log still streams into the CI transcript live; without it a
  # long build looks hung until it finishes.
  "$@" 2>&1 | tee "$log_file"
  status="${PIPESTATUS[0]}"

  if [ "$status" -eq 0 ]; then
    exit 0
  fi

  if [ "$attempt" -ge "$MAX_ATTEMPTS" ]; then
    printf '[retry-docker-build] giving up after %s attempt(s), exit status %s\n' \
      "$attempt" "$status" >&2
    exit "$status"
  fi

  if ! grep -Eqi "$TRANSIENT_PATTERN" "$log_file"; then
    printf '[retry-docker-build] failure does not look transient; not retrying\n' >&2
    exit "$status"
  fi

  printf '[retry-docker-build] transient failure detected, sleeping %ss before retry\n' \
    "$delay" >&2
  sleep "$delay"

  attempt=$((attempt + 1))
  delay=$((delay * 2))
  if [ "$delay" -gt "$MAX_DELAY" ]; then
    delay="$MAX_DELAY"
  fi
done

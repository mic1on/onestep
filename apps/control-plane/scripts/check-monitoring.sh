#!/usr/bin/env bash
# Validate the control-plane monitoring configuration.
#
# WHY THIS EXISTS
#
# The alert rules once shipped for months without ever being loaded or checked: no
# Prometheus config referenced them, and nothing in CI parsed them. A rule with a
# typo, a rule referencing a scrape job that does not exist, or a rule file that
# was never wired up all fail the same silent way -- no error, no alert. This
# script is the check that would have caught it.
#
# It validates three things, each with the upstream project's own tool:
#
#   1. prometheus.yml parses, its rule_files load, and every rule is well formed.
#   2. Every `job="..."` a rule selects on is defined in prometheus.yml. promtool
#      cannot see this -- it validates each file alone -- so it is checked here.
#   3. alertmanager.yml parses (routing + inhibition).
#
# USAGE
#
#   bash scripts/check-monitoring.sh
#
# Uses Docker so it needs no local promtool/amtool install. Set PROM_IMAGE /
# AM_IMAGE to pin different versions.

set -euo pipefail

cd "$(dirname "$0")/.."

PROM_IMAGE="${PROM_IMAGE:-prom/prometheus:v2.53.0}"
AM_IMAGE="${AM_IMAGE:-prom/alertmanager:v0.27.0}"

PROM_DIR="monitoring/prometheus"
AM_DIR="monitoring/alertmanager"

fail() {
  printf 'FAIL: %s\n' "$1" >&2
  exit 1
}

[ -f "$PROM_DIR/prometheus.yml" ] || fail "$PROM_DIR/prometheus.yml is missing"
[ -d "$PROM_DIR/rules" ] || fail "$PROM_DIR/rules/ is missing"
[ -f "$AM_DIR/alertmanager.yml" ] || fail "$AM_DIR/alertmanager.yml is missing"

# --- 1. promtool: config + rules ------------------------------------------------
#
# prometheus.yml points `credentials_file` at /etc/prometheus/ingest_token, which
# is written at container start by the compose entrypoint. promtool stats the file
# during validation, so the check runs against a copy with a dummy token: the
# token's VALUE is irrelevant to validity, only its presence is.
workdir="$(mktemp -d)"
cleanup() { rm -rf "$workdir"; }
trap cleanup EXIT

cp -R "$PROM_DIR/." "$workdir/"
printf 'validation-token' > "$workdir/ingest_token"

echo "==> promtool check config"
docker run --rm -v "$workdir:/etc/prometheus:ro" \
  --entrypoint /bin/promtool "$PROM_IMAGE" \
  check config /etc/prometheus/prometheus.yml

# --- 2. rule job names must exist in the scrape config ---------------------------
#
# This is the check that catches the original defect class. A rule selecting on
# `up{job="typo"}` is valid PromQL, loads without complaint, and simply never
# fires -- indistinguishable from a healthy system.
echo "==> every job= a rule selects on must be defined in prometheus.yml"

defined_jobs="$(
  grep -oE '^[[:space:]]*-[[:space:]]*job_name:[[:space:]]*[^[:space:]]+' "$PROM_DIR/prometheus.yml" \
    | sed -E 's/.*job_name:[[:space:]]*//; s/["[:space:]]//g' | sort -u
)"
[ -n "$defined_jobs" ] || fail "no job_name entries found in prometheus.yml"

referenced_jobs="$(
  grep -rhoE 'job[[:space:]]*=[[:space:]]*"[^"]+"' "$PROM_DIR/rules/" \
    | sed -E 's/.*"([^"]+)".*/\1/' | sort -u
)"

missing=""
while IFS= read -r job; do
  [ -n "$job" ] || continue
  if ! printf '%s\n' "$defined_jobs" | grep -qxF "$job"; then
    missing="$missing $job"
  fi
done <<< "$referenced_jobs"

if [ -n "$missing" ]; then
  printf 'FAIL: these jobs are selected by a rule but not defined in prometheus.yml:%s\n' "$missing" >&2
  printf '      Defined jobs:\n%s\n' "$defined_jobs" >&2
  exit 1
fi

printf 'OK: %s job(s) referenced, all defined\n' "$(printf '%s\n' "$referenced_jobs" | grep -c . || true)"

# --- 3. amtool: alertmanager config ---------------------------------------------
echo "==> amtool check-config"
docker run --rm -v "$PWD/$AM_DIR:/etc/alertmanager:ro" \
  --entrypoint /bin/amtool "$AM_IMAGE" \
  check-config /etc/alertmanager/alertmanager.yml

echo
echo "All monitoring configuration is valid."

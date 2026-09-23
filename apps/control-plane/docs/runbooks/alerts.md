# Alert Runbook

The Prometheus rules for the control plane live in
`monitoring/prometheus/rules/control-plane.yml`.

Some alerts depend on standard exporters:

- `up{job="onestep-control-plane"}` from the control plane scrape target
- `probe_success{job="onestep-control-plane-readyz"}` from a blackbox `/readyz` probe
- `pg_up{job="onestep-control-plane-postgres"}` from `postgres_exporter`

Every other alert reads a series emitted directly by the control plane's own
authenticated `/metrics` endpoint. No metrics pipeline or SQL exporter is required.

That is a checked property, not an aspiration: `backend/tests/test_prometheus_exporter.py`
parses this rule file and asserts that every `onestep_control_plane_*` series a rule
references is one the exporter can actually emit. If you add a rule for a series that
does not exist, that test fails.

Latency alerts and connection-failure triage use the series emitted directly by the
control plane `/metrics` endpoint (`onestep_control_plane_event_loop_lag_*`,
`onestep_control_plane_db_pool_*`, `onestep_control_plane_scan_*`). To tell event-loop
blocking apart from connection-pool wait or a slow database, follow
`docs/runbooks/control-plane-latency-diagnostics.md`.

## OneStepControlPlaneApiDown

Immediate meaning:

- the API target cannot be scraped by Prometheus

Operator actions:

1. Confirm container state with `docker compose ... ps`.
2. Check `docker compose ... logs plane`.
3. Check whether the control plane process crashed, the port bind changed, or the host is unreachable.
4. If the issue began during rollout, use `docs/runbooks/rollback.md`.

## OneStepControlPlaneReadyzFailing

Immediate meaning:

- the API is reachable, but `/readyz` is failing deep dependency checks

Operator actions:

1. Fetch `/readyz` directly and capture the JSON body.
2. Identify whether the failure is database, migration head, or background worker leadership.
3. If only one replica is unhealthy, compare it with the active leader replica.
4. If the failure is widespread, pause releases and prepare rollback.

## OneStepControlPlanePostgresDown

Immediate meaning:

- PostgreSQL is unreachable from the exporter

Operator actions:

1. Confirm database container or managed service status.
2. Check disk pressure, restart loops, and authentication failures.
3. If the database was recently changed, validate the DSN and credentials in `.env.deploy`.
4. If data recovery is required, switch to `docs/runbooks/backup-restore.md`.

## OneStepControlPlaneUiWsDisconnectSpike

Immediate meaning:

- console clients are dropping their event stream faster than the normal baseline

Naming caveat: the console stream is **server-sent events**
(`GET /api/v1/ui/stream`), not a websocket. The series keeps its historical `ui_ws`
spelling because this rule and dashboard refer to it by that name. The `reason` label
distinguishes the two causes: `client_closed` (the client or a proxy went away) and
`error` (the stream ended by raising).

Operator actions:

1. Check whether control plane restarts or reverse proxy reloads happened in the same window.
2. Compare the `reason` split: a spike concentrated in `error` points at the server or
   the broker; `client_closed` points at clients or a proxy dropping idle connections.
3. Compare console availability with `/readyz` and browser console errors.
4. Inspect upstream proxy timeouts and idle connection limits.
5. If disconnects correlate with deploys, slow or pause rollout traffic shifts.

## OneStepControlPlaneCommandFailureRateHigh

Immediate meaning:

- too many terminal control-plane commands are ending in failure states

Only **terminal** statuses are counted (`succeeded`, `failed`, `timeout`, `cancelled`,
`rejected`, `expired`). In-flight statuses (`pending`, `dispatched`, `accepted`) are
deliberately excluded so the denominator is not inflated by work that has not finished.

Operator actions:

1. Identify the failing command kinds and target services.
2. Check whether failures are agent-side rejections, timeouts, or runtime errors.
3. Validate agent websocket health and active session counts.
4. If failures affect destructive commands, temporarily restrict operator use until resolved.

## OneStepControlPlaneNotificationDeliveryFailures

Immediate meaning:

- webhook deliveries are failing and operators may be missing task failure signals

This counts delivery **attempts**, not channels: a webhook that fails and is re-queued
for retry increments `status="failed"` on every attempt, so a sustained outage fires
even when no delivery has exhausted its retries yet.

Operator actions:

1. Query recent `notification_deliveries` rows with `status='failed'`.
2. Check destination webhook availability and rate limiting.
3. Verify whether failures are isolated to one channel or affect all configured channels.
4. If alerts are suppressed externally, use alternate notification paths until fixed.

## OneStepControlPlaneEventLoopBlocked

Immediate meaning:

- synchronous work is running on the asyncio event loop and delaying every task on it

Reads `onestep_control_plane_event_loop_lag_p95_seconds`, not the latest sample: a
stall that already ended still shows in the p95/max window while the latest sample
looks healthy. Threshold (200 ms) matches the latency diagnostics runbook.

Operator actions:

1. Follow `docs/runbooks/control-plane-latency-diagnostics.md` section 3 to separate
   loop blocking from pool wait and a slow database.
2. Check whether `onestep_control_plane_scan_duration_seconds` moved in the same window.
3. Capture `py-spy dump` on the process while the gauge is high.
4. If the pool and scan metrics are flat, treat it as a host/CPU/GC problem.

## OneStepControlPlaneEventLoopLagSamplerDead

Immediate meaning:

- the lag sampler task is not alive, so **every lag gauge is stale**

This means *no data*, not *no lag*. A quiet lag graph is unproven until this clears;
do not read a flat lag line as evidence of health while this is firing.

Operator actions:

1. Confirm the process is the API (a worker script that never runs the lifespan will
   not start the sampler; the `/metrics` endpoint starts it as a fallback on first scrape).
2. Check for an unhandled exception in the sampler task in the logs.
3. Restart the process if the sampler does not recover on the next scrape.

## OneStepControlPlaneDbPoolSaturated

Immediate meaning:

- more than 90% of the database connection pool has been checked out for 10 minutes

Operator actions:

1. Check for leaked sessions (a checkout with no matching checkin) **before** raising
   `pool_size`; a leak will consume any size you configure.
2. Compare with `onestep_control_plane_db_pool_wait_seconds` to confirm callers are
   actually waiting.
3. Check `onestep_control_plane_db_pool_overflow` to see whether the pool is already
   running above its configured size.

## OneStepControlPlaneDbPoolWaitSlow

Immediate meaning:

- more than 10% of pool checkouts waited at least 100 ms (the module's slow threshold)

Operator actions:

1. Use the occupancy gauges to separate slot contention from slow connection setup:
   high `checked_out` means contention, low `checked_out` with slow waits means
   connection setup or pre-ping is the cost.
2. Check database-side latency and `pg_stat_activity`.
3. Note the documented blind spot: `pool.connect()` duration bundles slot wait, new
   DBAPI connection setup and pool pre-ping into one number.

## OneStepControlPlaneScanFailing

Immediate meaning:

- a background scan has raised in the last 15 minutes

This is the most operationally serious of the latency alerts: missed-start and
instance-connectivity notifications are produced **by** these scans, so a failing scan
means those alerts are not being evaluated at all — silence does not mean health.

Operator actions:

1. Grep the logs for `scan_run_failed` to get the `scan` name and the exception.
2. Check database connectivity and whether a migration or schema change broke the query.
3. Confirm the scanner still holds leadership (a lease failure is a different fault).

## OneStepControlPlaneScanSlow

Immediate meaning:

- p95 duration of a background scan has exceeded 5 seconds for 15 minutes

Operator actions:

1. Grep the logs for `scan_run_slow` to identify which scan.
2. Check row counts and query plans for that scan; the notification scans walk
   instances and task definitions.
3. Check whether the database is slow generally (`db_pool_wait_seconds`), in which case
   the scan is a symptom rather than the cause.
4. Remember scans are bounded by `notification_missed_start_scan_interval_s` (60 s by
   default), so a scan that runs longer than its interval delays the next one.

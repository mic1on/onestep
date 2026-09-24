# Alert Runbook

The Prometheus rules for the control plane live in
`monitoring/prometheus/rules/control-plane.yml`.

## Running the alerting stack

The rules only fire if something loads them. `monitoring/prometheus/prometheus.yml`
declares `rule_files` and defines the three scrape jobs the rules select on, and
`docker-compose.monitoring.yml` brings up Prometheus, Alertmanager, a blackbox
exporter and a postgres exporter:

```bash
cd apps/control-plane
docker compose -f docker-compose.yml -f docker-compose.monitoring.yml up -d
# Prometheus:   http://127.0.0.1:9090
# Alertmanager: http://127.0.0.1:9093
```

Set your notification endpoint in the `default-webhook` receiver of
`monitoring/alertmanager/alertmanager.yml`. Its shipped default is a reserved
`.invalid` host (RFC 2606) that cannot resolve, so a forgotten configuration fails
loudly at delivery time instead of dropping alerts silently.

All three monitoring ports bind to `127.0.0.1` only. Prometheus and Alertmanager
have no authentication of their own.

Validate any change to these files before shipping:

```bash
bash scripts/check-monitoring.sh
```

That runs `promtool check config` (config + all rules), `amtool check-config`
(Alertmanager), and one check `promtool` cannot do: **every `job="..."` a rule
selects on must be defined in the scrape config**. A rule selecting on a
misspelled job name is valid PromQL, loads without complaint, and never fires —
which is exactly how twelve rules once shipped for months without ever being
loaded. The same check runs in CI (`monitoring` job) and in the backend suite.

## Jobs the rules depend on

| Job | Source | Series the rules read |
| --- | --- | --- |
| `onestep-control-plane` | the API's own `/metrics` (bearer token) | all `onestep_control_plane_*` |
| `onestep-control-plane-readyz` | blackbox probe of `/readyz` | `probe_success` |
| `onestep-control-plane-postgres` | postgres_exporter | `pg_up` |

The `/metrics` endpoint requires a bearer token (`require_ingest_token`). Prometheus
cannot expand environment variables inside its own config, so the compose
entrypoint writes the first `ONESTEP_CP_INGEST_TOKENS` value to
`/etc/prometheus/ingest_token`, which the scrape config reads via
`credentials_file`.

## Alert routing and inhibition

`monitoring/alertmanager/alertmanager.yml` carries two things the rules cannot
express alone:

- **Routing** — `critical` repeats hourly (a page), `warning` repeats every 12
  hours (a ticket). The rules only carry `severity` and `service` labels.
- **Inhibition** — most of these alerts are *derived* from a small number of root
  causes. `OneStepControlPlaneApiDown` inhibits the eleven alerts that are its
  consequences, so an operator gets one page naming the cause instead of a dozen.
  `OneStepControlPlanePostgresDown` inhibits only the database-derived symptoms
  (scans, pool), deliberately leaving command and notification failures visible —
  those have causes other than the database.

### What the two severity tiers mean

`critical` covers two classes, and both are worth waking someone:

1. **The control plane is down** — `ApiDown`, `ReadyzFailing`, `PostgresDown`.
2. **It is up but has lost the ability to tell you about problems** —
   `ScanFailing`, `NotificationDeliveryFailures`, `MetricsMissing`,
   `ScanNeverRan`.

The second class is why severity was re-tiered. A failing scan or failed delivery
does not merely degrade a metric: it stops the notification plane from reporting,
so the operator goes blind *without being told*. That is strictly more dangerous
than an outage that announces itself. Both carry `for: 15m` over a 15m window, so
the condition must hold for roughly 30 minutes — not a page on a single blip.

`warning` is everything that degrades the system while leaving it observable:
websocket disconnect spikes, command failure rate, latency and pool pressure.

### Absence guards: "no data" is not "healthy"

Four rules in the `onestep-control-plane-absence` group exist because a PromQL
expression over a **missing** series evaluates to *no data*, which fires nothing and
looks identical to a healthy system on a graph. Several families are emitted lazily:

| Guard | Fires when |
| --- | --- |
| `MetricsMissing` | the target is up but exports no observability families at all |
| `ScanNeverRan` | no scan has ever completed, so `ScanFailing` cannot evaluate |
| `LagWindowEmpty` | the lag sampler runs but the rolling window holds no samples |
| `DbPoolOccupancyMissing` | the scrape-time occupancy sampling is failing |

Each is gated on `up == 1`, which makes it **mutually exclusive with `ApiDown`** by
construction — a guard answers "the target is up but its data is gone", while
`ApiDown` answers "the target is gone". They can never both be true, which is why
the guards are deliberately absent from `ApiDown`'s inhibit list.

They use `and on()`, and that is load-bearing rather than decoration: `absent()`
returns a series with an **empty label set**, and a bare `and` matches on all labels,
so `up{job="x"} and absent(...)` matches nothing and the guard would never fire. The
trap was verified against Prometheus 2.53 before these rules were written, and
`test_absence_guards_use_and_on` now pins it.

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
even when no delivery has exhausted its retries yet. For the subset that will never be
retried, see `OneStepControlPlaneNotificationLost`.

Operator actions:

1. Query recent `notification_deliveries` rows with `status='failed'`.
2. Check destination webhook availability and rate limiting.
3. Verify whether failures are isolated to one channel or affect all configured channels.
4. If alerts are suppressed externally, use alternate notification paths until fixed.

## OneStepControlPlaneNotificationLost

Immediate meaning:

- a notification exhausted its retry budget and was **abandoned** — it will never be sent

This is the strictly worse sibling of the alert above. A transient delivery failure is
retried, so the operator may still end up informed; a permanently failed delivery means
that specific task failure, missed start or instance offline event reached **nobody**,
and the operator has no way to know from inside the console.

The distinction is carried by the `status` label: `permanently_failed` is a separate
series from `failed` precisely so this can be strict (`for: 5m`, nothing to wait out)
without the attempt-level alert firing on every blip.

Operator actions:

1. Query `notification_outbox` rows with `status='permanently_failed'` and note the
   destinations and `last_error`.
2. Treat the affected events as **unreported**: if a task failure or instance offline
   happened in that window, nobody was told. Check the console for what was missed.
3. Fix the destination, then confirm with the channel's test button — it performs a
   real request and reports the actual HTTP outcome.
4. Consider raising `notification_outbox_max_attempts` only after understanding why
   every attempt failed; more retries do not fix a wrong URL or an expired token.

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

## OneStepControlPlaneMetricsMissing

Immediate meaning:

- the scrape target is **up**, but the control plane exports no observability metrics

Every alert that reads those families is therefore silent, and silence reads as
healthy. This is the "who watches the watcher" alert.

Not the bearer-token case: a wrong token makes `/metrics` answer 401, which marks the
target **down** and raises `OneStepControlPlaneApiDown` instead. This fires when the
scrape succeeds but the process exports nothing.

Operator actions:

1. Fetch `/metrics` with the configured token and confirm whether families are present.
2. Check whether the app started its observability samplers (lifespan startup).
3. Look for an import error or an exception in the metrics exporter path.

## OneStepControlPlaneScanNeverRan

Immediate meaning:

- no background scan has completed since this process started

Missed-start and instance-connectivity notifications are produced by these scans.
While this fires, those alerts are not merely quiet — they are not being evaluated.

Operator actions:

1. Check scanner leadership: a replica that never wins the advisory lock never scans.
2. Check the scanner's readiness state on `/readyz` (`background_tasks`).
3. Confirm the scan interval setting is not absurdly large.

## OneStepControlPlaneLagWindowEmpty

Immediate meaning:

- the event-loop lag sampler is running but the rolling window holds no samples

`OneStepControlPlaneEventLoopBlocked` reads the p95/max gauges, which are absent
while the window is empty, so lag is currently unmonitored.

Operator actions:

1. Confirm the sampler task is alive (`..._lag_sampler_running`).
2. Check the sample interval; a very large interval delays the first sample.
3. Look for an exception inside the sampler loop.

## OneStepControlPlaneDbPoolOccupancyMissing

Immediate meaning:

- the pool occupancy gauges are absent, so the scrape-time sampling step is failing

`OneStepControlPlaneDbPoolSaturated` reads these gauges, so pool saturation is
unmonitored while this fires.

Operator actions:

1. Grep the control plane logs for "could not sample pool occupancy".
2. Confirm the engine is instrumented (`db/session.py` factory).
3. Verify the pool exposes the SQLAlchemy occupancy accessors; a `StaticPool` does not.

## OneStepControlPlaneNotificationQueueStuck

Immediate meaning:

- outbox rows are **due** (`next_attempt_at` in the past) but not being delivered

The outbox worker samples this on each tick. The metric is the **age of the oldest
due row**, not the queue depth, because depth alone cannot tell "busy" from "stuck":
a deep queue that is draining is healthy, while a single row that has been due for
fifteen minutes means the drainer has stopped.

Operator actions:

1. Check outbox worker leadership — only the leader replica drains, so a replica that
   cannot take the advisory lock will never drain.
2. Look for drain failures in the logs (`notification outbox worker drain failed`).
3. Check the destination webhook: a hung downstream delays rows even when nothing is
   permanently failed.
4. Confirm `notification_outbox_drain_interval_s` and `notification_outbox_batch_size`
   against the arrival rate; a batch smaller than the arrival rate produces a queue
   that never catches up.

## OneStepControlPlaneNotificationQueueUnsampled

Immediate meaning:

- the outbox backlog gauge has **never** been exported, so the drainer has not
  completed a single tick since this process started

The backlog gauges are sampled by the outbox worker, not by the scrape path. Their
absence therefore means "the worker has never run", not "the queue is empty" — which
is why no zero is emitted for an unsampled process.

Operator actions:

1. Confirm the outbox worker task is registered and running (`/readyz`
   `background_tasks`).
2. Check whether it is stuck waiting for leadership it can never acquire.
3. Check for an exception at worker startup.


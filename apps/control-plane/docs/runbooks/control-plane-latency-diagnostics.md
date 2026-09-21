# Control-Plane Latency Diagnostics Runbook

Use this runbook when a websocket connection, a database call, or a background
scan looks slow and you need to tell **which** of three different problems you
are looking at:

1. the asyncio event loop is blocked by synchronous work,
2. a database checkout is waiting on the SQLAlchemy connection pool,
3. the database itself is slow.

The instrumentation behind this runbook is the baseline delivered by issue
**#197** in
`apps/control-plane/backend/src/onestep_control_plane_api/ops/observability.py`
and exported through the existing `/metrics` endpoint. It is deliberately small:
no new agent, no new exporter, no new platform.

## 1. What the baseline exposes today

| Metric | Type | Unit | Meaning |
| --- | --- | --- | --- |
| `onestep_control_plane_event_loop_lag_seconds` | gauge | seconds | Most recent loop-lag sample. |
| `onestep_control_plane_event_loop_lag_p95_seconds` | gauge | seconds | p95 over the rolling window (240 samples, 60 s at 0.25 s). |
| `onestep_control_plane_event_loop_lag_max_seconds` | gauge | seconds | Worst sample in the window. |
| `onestep_control_plane_event_loop_lag_window_samples` | gauge | count | Samples currently retained. |
| `onestep_control_plane_event_loop_lag_sample_interval_seconds` | gauge | seconds | Configured wake-up interval (0.25 s). |
| `onestep_control_plane_event_loop_lag_sampler_running` | gauge | 0/1 | 1 while the sampler task is alive. |
| `onestep_control_plane_db_pool_wait_seconds` | histogram | seconds | Wall time inside `pool.connect()` per checkout. |
| `onestep_control_plane_db_pool_checkouts_total` | counter | count | Instrumented checkouts observed. |
| `onestep_control_plane_db_pool_wait_slow_total` | counter | count | Checkouts at or above 0.1 s. |
| `onestep_control_plane_db_pool_checked_out` | gauge | count | Connections currently checked out. |
| `onestep_control_plane_db_pool_checked_in` | gauge | count | Idle connections held in the pool. |
| `onestep_control_plane_db_pool_overflow` | gauge | count | Connections opened above `pool_size`. |
| `onestep_control_plane_db_pool_size` | gauge | count | Configured pool size. |
| `onestep_control_plane_db_pool_occupancy_timestamp_seconds` | gauge | unix s | When occupancy was last sampled. |
| `onestep_control_plane_scan_duration_seconds` | histogram | seconds | Duration of one background scan run. |
| `onestep_control_plane_scan_runs_total` | counter | count | Scan runs observed. |
| `onestep_control_plane_scan_failures_total` | counter | count | Scan runs that raised. |

Structured log events (identity is correlated here, never in labels):

| Event | Level | Correlation fields |
| --- | --- | --- |
| `agent_ws_connected` / `agent_ws_disconnected` / `agent_ws_rejected` / `agent_ws_error` | INFO | `instance_id`, `session_id`, `close_code`, `close_code_known`, `close_reason`, `close_reason_known`, `connection_duration_s` |
| `db_pool_wait_slow` | WARNING | `pool_class`, `pool_name`, `wait_s`, `threshold_s` |
| `scan_run_slow` | WARNING | `scan`, `duration_s`, `threshold_s`, `started_at`, `finished_at` |
| `scan_run_failed` | WARNING | `scan`, `duration_s`, `outcome` |

Every record carries `event` and `logged_at` (ISO-8601 UTC), so log lines and
metric timestamps can be put on one clock.

> **Status of the wiring.** Since #213 the wiring is complete for the two
> production seams: the application lifespan starts the lag sampler on startup
> and stops it on shutdown (the `/metrics` endpoint still starts it as a
> fallback for anything that never runs the lifespan), and the synchronous
> engine factory (`db/session.py`) instruments the sync pool under
> `name="default"` while the notification scanner instruments the async pool
> under `name="async"`. The pool histogram and the lag gauges are therefore
> populated from process start, without waiting for a scrape.

## 2. Reproducible diagnostic session

The example below is a self-contained fault-injection session. It uses the
module directly so it can run anywhere, including a laptop or CI, with no
deployment change.

### Step 0 — prepare a scratch script

```bash
cd apps/control-plane
cat > /tmp/onestep-197-diagnose.py <<'PY'
import asyncio, logging, threading, time
import sqlalchemy as sa
from sqlalchemy.pool import QueuePool

# Import the API package first: onestep_control_plane_api.ops has a pre-existing import
# cycle with api.routers.health (present on main before #197), so a bare
# `from onestep_control_plane_api.ops import observability` fails in a fresh process.
import onestep_control_plane_api.api.routers.prometheus  # noqa: F401
from onestep_control_plane_api.api.routers.prometheus import build_observability_metrics
from onestep_control_plane_api.ops import observability as obs

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s %(logged_at)s")

engine = sa.create_engine(
    "sqlite+pysqlite:////tmp/onestep-197-diagnose.db",
    connect_args={"check_same_thread": False},
    poolclass=QueuePool, pool_size=1, max_overflow=0,
)
obs.instrument_engine(engine, name="diagnose")
obs.refresh_pool_occupancy(engine, name="diagnose")

async def main() -> None:
    obs.ensure_event_loop_lag_sampler_started()
    await asyncio.sleep(0.5)

    # inject pool contention: hold the only connection for 0.5s
    holder = engine.raw_connection()
    def release() -> None:
        time.sleep(0.5)
        holder.close()
    threading.Thread(target=release).start()

    started = time.perf_counter()
    second = engine.raw_connection()          # blocks on the pool
    print(f"measured checkout wait: {time.perf_counter() - started:.3f}s")
    second.close()

    # inject event-loop blocking: synchronous work inside the loop
    time.sleep(0.4)
    await asyncio.sleep(0.5)

    # inject a slow scan
    with obs.scan_duration_timer("notification_missed_start"):
        time.sleep(1.2)
    await asyncio.sleep(0.5)

    obs.refresh_pool_occupancy(engine, name="diagnose")
    print(build_observability_metrics())

asyncio.run(main())
PY
uv run --no-sync python /tmp/onestep-197-diagnose.py | tee /tmp/onestep-197-diagnose.txt
```

### Step 1 — read the three signals together

From the captured output:

```bash
grep -E 'event_loop_lag_(seconds|p95_seconds|max_seconds) |db_pool_wait_seconds_(count|sum)|db_pool_checked_out|scan_duration_seconds_(count|sum)' \
  /tmp/onestep-197-diagnose.txt
```

Values captured from one actual run of this script (the *relationships* are the
point; the exact numbers move with machine speed):

```text
measured checkout wait: 0.510s
onestep_control_plane_event_loop_lag_seconds 0.00082                    # loop healthy at scrape time
onestep_control_plane_event_loop_lag_p95_seconds 1.204                  # ... but it was blocked earlier
onestep_control_plane_event_loop_lag_max_seconds 1.204
onestep_control_plane_event_loop_lag_window_samples 5
onestep_control_plane_db_pool_wait_seconds_count{name="diagnose",pool="QueuePool"} 2
onestep_control_plane_db_pool_wait_seconds_sum{name="diagnose",pool="QueuePool"} 0.5109
onestep_control_plane_db_pool_checked_out{name="diagnose",pool="QueuePool"} 0
onestep_control_plane_db_pool_wait_slow_total{name="diagnose",pool="QueuePool"} 1
onestep_control_plane_scan_duration_seconds_sum{scan="notification_missed_start"} 1.2040
onestep_control_plane_scan_duration_seconds_count{scan="notification_missed_start"} 1
```

Three things this single run demonstrates:

1. `db_pool_wait_seconds_sum` (0.5109 s) agrees with the wait the script measured
   itself (0.510 s) — the histogram is a measurement, not an estimate.
2. The pool bucket at `le="0.5"` stayed at 1 while `le="1"` jumped to 2: exactly
   one of the two checkouts waited longer than 500 ms.
3. `event_loop_lag_seconds` (latest, 0.00082) is *healthy* while `p95`/`max`
   (1.204) are not. The blocking already ended before the scrape. **Reading only
   the latest sample would have missed the stall** — this is why the window
   metrics exist.

### Step 2 — correlate with the logs

The same run emitted (with `logging.basicConfig(level=logging.INFO)`):

```text
WARNING db_pool_wait_slow 2026-09-20T09:57:44.535164+00:00
WARNING scan_run_slow 2026-09-20T09:57:46.644540+00:00
```

In a deployment, grep the container logs:

```bash
docker compose logs --timestamps --since=10m plane \
  | grep -E '"event": ?"(agent_ws_|db_pool_wait_slow|scan_run_)'
```

Then line up the three timelines by `logged_at` (logs) and by the scrape
timestamp (metrics):

```text
T+0.00s  agent_ws_connected    instance_id=... session_id=...      <- WS lifecycle
T+0.50s  db_pool_wait_slow     pool=diagnose wait_s=0.510          <- pool contention
T+0.90s  (loop blocked)        event_loop_lag_max_seconds 1.204   <- loop blocking
T+1.60s  scan_run_slow         scan=notification_missed_start duration_s=1.204
```

## 3. Telling pool wait from event-loop blocking

This is the decision table the issue asks for. Read it against one fault window.

| Observation | Most likely cause | Why |
| --- | --- | --- |
| `db_pool_wait_seconds` p95 high **and** `event_loop_lag_*` flat | **Pool wait.** The loop is healthy; callers are queuing for a connection. | A blocking `pool.connect()` inside a threadpool/worker does not stall the loop. |
| `event_loop_lag_*` high **and** `db_pool_wait_seconds` flat, with `db_pool_checked_out` low | **Event-loop blocking.** Sync work is running on the loop. | The lag sampler is late, but the pool was never contended. |
| Both high in the same window | Loop blocking is *also* delaying checkins/checkouts, **or** two independent faults overlap. | Use `db_pool_checked_out` and `db_pool_checked_in` to see whether the pool was actually saturated. |
| `db_pool_wait_seconds` high, `db_pool_checked_out` at `pool_size + overflow`, `overflow` at its max | **Pool saturation.** | Occupancy proves every slot was in use. |
| `db_pool_wait_seconds` high, `db_pool_checked_out` low, `overflow` 0 | **Slow connection setup**, not slot contention. | `pool.connect()` also covers opening a new DBAPI connection and pre-ping. |
| `db_pool_wait_seconds` flat, `scan_duration_seconds` high, `event_loop_lag_*` high | **Slow database inside the scan**, exposed as loop blocking. | The scan held the loop while the query ran. |
| `db_pool_wait_seconds` flat, `scan_duration_seconds` high, `event_loop_lag_*` flat | **Slow scan work that is not on the loop.** | Look at query plans and row counts, not at the loop. |

Two rules that keep this honest:

- **Correlation is not causation.** A `db_pool_wait_slow` line at T+0.5 s and a
  disconnect at T+0.52 s are *coincident*. They are evidence for a hypothesis,
  not proof that the pool wait caused the disconnect. To turn it into a causal
  claim you need a controlled reproduction (inject only the pool wait, observe
  whether the disconnect still happens).
- **Prefer the counter over the log line.** `db_pool_wait_slow_total` counts
  every slow wait; the WARNING line is emitted from the same code path, so if the
  counter is flat the log line is not the story.

## 4. Reconnect timing

The reconnect side of the story is a *log* signal, not a metric, because it is
identity-scoped and would blow up label cardinality:

```bash
docker compose logs --timestamps --since=10m plane \
  | grep -E 'agent_ws_(connected|disconnected|rejected|error)'
```

For each pair, read:

- `instance_id` / `session_id` — which connection this is (correlation only).
- `connection_duration_s` — how long the session survived.
- `close_code` / `close_code_known` — a real close code, or `false` meaning the
  code was not observable at the point of logging.
- `close_reason` / `close_reason_known` — `"unknown"` when the peer did not
  supply one. **`unknown` means unknown**: it is not a clean close.

A reconnect storm shows up as many `agent_ws_disconnected` records with short
`connection_duration_s` inside the same window as a latency signal. Compare the
window against the three metric families above before blaming either side.

## 5. Observational blind spots

State these explicitly when you write up a diagnosis; none of them are bugs, but
each one limits what the evidence can prove.

1. **`pool.connect()` duration is a bundle.** It covers slot wait, new DBAPI
   connection setup and pool pre-ping. It cannot, by itself, separate "waited for
   a slot" from "opened a slow connection". Use the occupancy gauges to tell them
   apart (see the table in section 3).
2. **Occupancy is sampled, not continuous.** Gauges reflect the instant of the
   scrape. A pool that saturates between two scrapes is invisible.
3. **Lag is sampled at 0.25 s.** A blocking stall shorter than ~0.25 s may land
   between two wake-ups and be under-reported. `max_s` over the window is the
   sensitive signal; `p50` is not.
4. **Lag conflates causes.** The sampler sees "the loop was late", not "the loop
   was late because of X". GC pauses, CPU starvation, thread-pool saturation and
   a synchronous database call all look the same.
5. **The sampler only runs if something starts it.** Since #213 the application
   lifespan starts it at startup and stops it at shutdown, so
   `event_loop_lag_sampler_running 0` means either the process is not the API
   (a worker script that never runs the lifespan) or the sampler task died.
   In both cases the gauge means *no data*, not *no lag*; the `/metrics`
   endpoint still starts the sampler as a fallback on its first scrape.
6. **Per-process scope.** Every metric is process-local. With multiple replicas,
   aggregate per pod; a single scrape tells you nothing about the other replicas.
7. **Counters reset on restart.** `*_total` values are per-process and restart at
   zero; use `rate()`/`increase()`, never raw values, across a restart.
8. **Scans are only measured where the timer is called.** A scan that never
   enters `scan_duration_timer` contributes nothing; absence of data is not
   evidence of a fast scan.
9. **Scan names are allowlisted.** Unregistered names collapse to `scan="other"`,
   so a new scan can appear as `other` until it is registered.
10. **Logs are best-effort.** A slow wait is logged at WARNING through the normal
    logging path; if the log pipeline drops or samples records, the counter is
    still authoritative.
11. **Clock domains differ.** Log `logged_at` comes from the process wall clock;
    `perf_counter()` durations are monotonic. Compare durations, and treat
    cross-host timestamp comparison as approximate unless clocks are synced.
12. **No metric for identity.** By design. Anything identity-scoped (a specific
    session's lifecycle) must be reconstructed from logs, not from the metric
    store.
13. **Pre-existing import cycle.** `onestep_control_plane_api.ops.__init__`
    imports `ops.readiness`, which imports workers that re-enter
    `api.routers.health`, which imports `ops.readiness` again. Importing
    `onestep_control_plane_api.ops.observability` in a *fresh* interpreter
    therefore fails unless the API package is imported first. This cycle exists
    on `main` before #197 and is not introduced here; scripts should import
    `onestep_control_plane_api.api.routers.prometheus` (or any `api` module) first.
14. **Series are keyed by pool name, not pool object.** Two engines instrumented
    with the same `name` (including the default) share one `{name, pool}` series:
    their waits aggregate into the same histogram, and the occupancy gauge shows
    whichever was sampled last. Give each engine a distinct `name` when running
    more than one, otherwise a busy engine can be hidden behind a quiet one.
15. **Gauge staleness.** `db_pool_occupancy_timestamp_seconds` tells you when the
    occupancy gauges were last refreshed. If that timestamp stops advancing, the
    occupancy numbers are stale — check that something still calls
    `refresh_pool_occupancy` (only the follow-up wiring PR will do so
    periodically in production).
16. **The log scrubber is name- and shape-based, and that is a real limit.**
    `build_log_fields` redacts a value in two cases only: its **field name**
    matches `SENSITIVE_FIELD_PATTERN` (token, authorization, password,
    auth_header, cookie, credential, api_key, private_key, dsn, database_url,
    body, payload, raw_message, message_body), or its **string form** contains a
    `Bearer <token>` / `token=<value>` *shape*. Consequences to state in any
    write-up that relies on log evidence:

    * a bare secret under a **non**-sensitive key name passes through verbatim —
      `{"note": "hunter2"}` is logged as `hunter2`, because the value has neither
      a sensitive name nor a credential shape;
    * a connection string under a non-sensitive name passes through too —
      `{"target": "postgresql://user:pw@host/db"}` is not caught, while the same
      string under `database_url` or `dsn` is;
    * nesting is walked only within `MAX_SANITIZE_DEPTH` (12) levels and within a
      cycle guard. Deeper than that the whole remaining subtree is replaced by
      `[redacted]` wholesale rather than described, so a deep structure cannot
      leak through the string fallback — but it also means a deep structure is
      *not* usefully logged. Reduce the depth at the call site if you need the
      contents.

    Treat "no secret appears in the logs" as a statement about these three
    mechanisms, not as proof that no secret was ever passed to a log call.

## 6. Useful PromQL

```promql
# p95 checkout wait per pool over 5m
histogram_quantile(0.95, sum by (le, name, pool) (rate(onestep_control_plane_db_pool_wait_seconds_bucket[5m])))

# fraction of checkouts that waited more than 100 ms
sum by (name) (rate(onestep_control_plane_db_pool_wait_slow_total[5m]))
  / sum by (name) (rate(onestep_control_plane_db_pool_checkouts_total[5m]))

# pool saturation
onestep_control_plane_db_pool_checked_out / onestep_control_plane_db_pool_size

# event loop blocking (sustained)
onestep_control_plane_event_loop_lag_p95_seconds > 0.2

# sampler died -> data is stale, not healthy
onestep_control_plane_event_loop_lag_sampler_running == 0

# scan duration p95
histogram_quantile(0.95, sum by (le, scan) (rate(onestep_control_plane_scan_duration_seconds_bucket[15m])))
```

## 7. Escalation

- Pool saturation that does not clear with traffic: check for leaked sessions
  (a checkout without a matching checkin) before raising `pool_size`.
- Loop blocking that is not explained by a scan or a request path: capture
  `py-spy dump` on the process while the lag gauge is high.
- Persistent lag with flat pool and scan metrics: treat it as a host/CPU/GC
  problem, not a database problem.

---
title: Metrics & Health Checks | Guide
outline: deep
---

# Metrics & Health Checks

onestep ships a dependency-free asyncio HTTP server that exposes Prometheus `/metrics` and `/healthz` endpoints. The `onestep[metrics]` extra has no additional dependencies of its own; it only provides a stable opt-in path for orchestrators and documentation.

## Starting the Metrics Endpoint

Use `--metrics-addr` to specify the listen address:

```bash
onestep run your_package.tasks:app --metrics-addr :9100
```

Address forms:

- `HOST:PORT` — bind a specific address, e.g. `127.0.0.1:9100`
- `:PORT` — bind all interfaces, e.g. `:9100`
- `PORT` — equivalent to `127.0.0.1:PORT`

When no host is given, it defaults to `127.0.0.1`; the default port is `9100`.

## /metrics

`/metrics` emits Prometheus text format with the following families (all carrying `app` and `task` labels):

| Metric | Type | Description |
|---|---|---|
| `onestep_deliveries_fetched_total` | counter | deliveries fetched from sources |
| `onestep_tasks_processed_total` | counter | terminal task outcomes (by `status` label) |
| `onestep_task_duration_seconds` | histogram | task attempt duration |
| `onestep_inflight_tasks` | gauge | task attempts currently in flight |
| `onestep_tasks_retried_total` | counter | task attempts scheduled for retry |
| `onestep_tasks_dead_lettered_total` | counter | deliveries published to dead-letter sinks |
| `onestep_tasks_cancelled_total` | counter | task attempts cancelled |
| `onestep_task_failures_total` | counter | task failures by `failure_kind` |
| `onestep_build_info` | gauge | build metadata |

Task handlers can also report custom counters/gauges through `ctx.metrics`:

```python
async def sync_users(ctx, payload):
    ...
    ctx.metrics.counter("rows_success").inc(1)
    ctx.metrics.gauge("batch_size").set(42)
```

These custom metrics appear in `/metrics` with a `task` label plus any user labels.

## /healthz

`/healthz` returns a JSON liveness payload:

```json
{
  "status": "ok",
  "app": "billing-sync",
  "version": "1.12.0",
  "uptime_s": 120.5,
  "stopping": false,
  "tasks": [
    {
      "task": "sync",
      "source": {"name": "orders", "kind": "RabbitMQQueue", "alive": true},
      "inflight": 2
    }
  ]
}
```

- `status` is `ok` when all sources are alive and the app is not stopping; otherwise `degraded`.
- `source.kind` is the source object's class name; `source.alive` reflects that source's `is_open` state.
- `inflight` is the task's in-flight attempt count.

Load balancers can use this for readiness/liveness probes.

## Embedded Use

Without the CLI, you can install metrics directly in code:

```python
from onestep.observability import install_metrics

handle = install_metrics(app, host="0.0.0.0", port=9100)
...
await handle.close()
```

`install_metrics` registers the event handler, binds the listener after resources open, and releases it on shutdown. `port=0` auto-selects a port for tests and embedded use.

## Example

A complete runnable Prometheus + Grafana stack lives in the repository's [`examples/prometheus/`](https://github.com/mic1on/onestep/tree/main/examples/prometheus).

## Next Steps

- [Logging & Task Events](/en/guide/logging) - `--log-format json` structured logs
- [Production Deploy](/en/guide/deploy) - CLI and container deployment
- [Control Plane](/en/control-plane/) - reporter telemetry and remote task control

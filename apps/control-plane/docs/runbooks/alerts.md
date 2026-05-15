# Alert Runbook

The Prometheus rules for the control plane live in
`monitoring/prometheus/rules/control-plane.yml`.

Some alerts depend on standard exporters:

- `up{job="onestep-control-plane-api"}` from the direct API scrape target
- `probe_success{job="onestep-control-plane-readyz"}` from a blackbox `/readyz` probe
- `pg_up{job="onestep-control-plane-postgres"}` from `postgres_exporter`

Other alerts depend on a metrics pipeline or SQL exporter that emits the following series:

- `onestep_control_plane_ui_ws_disconnects_total`
- `onestep_control_plane_agent_commands_total{status=...}`
- `onestep_control_plane_notification_deliveries_total{status=...}`

## OneStepControlPlaneApiDown

Immediate meaning:

- the API target cannot be scraped by Prometheus

Operator actions:

1. Confirm container state with `docker compose ... ps`.
2. Check `docker compose ... logs api`.
3. Check whether the API process crashed, the port bind changed, or the host is unreachable.
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

- console websocket clients are disconnecting faster than the normal baseline

Operator actions:

1. Check whether API restarts or reverse proxy reloads happened in the same window.
2. Compare frontend availability with `/readyz` and browser console errors.
3. Inspect upstream proxy timeouts and idle connection limits.
4. If disconnects correlate with deploys, slow or pause rollout traffic shifts.

## OneStepControlPlaneCommandFailureRateHigh

Immediate meaning:

- too many terminal control-plane commands are ending in failure states

Operator actions:

1. Identify the failing command kinds and target services.
2. Check whether failures are agent-side rejections, timeouts, or runtime errors.
3. Validate agent websocket health and active session counts.
4. If failures affect destructive commands, temporarily restrict operator use until resolved.

## OneStepControlPlaneNotificationDeliveryFailures

Immediate meaning:

- webhook deliveries are failing and operators may be missing task failure signals

Operator actions:

1. Query recent `notification_deliveries` rows with `status='failed'`.
2. Check destination webhook availability and rate limiting.
3. Verify whether failures are isolated to one channel or affect all configured channels.
4. If alerts are suppressed externally, use alternate notification paths until fixed.

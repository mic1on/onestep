# Changelog

## 1.4.2

- Requires strict YAML `memory` resources to set a bounded `maxsize`.
- Caps queued interval and cron schedule runs with `max_queued_runs`, defaulting to `1000`.
- Limits Feishu Bitable incremental fallback scans with `fallback_scan_page_limit`, defaulting to `100` pages.
- Raises the Feishu Bitable plugin package version to `0.1.2` and requires `onestep>=1.4.2`.

## 1.4.1

- Keeps connector resilience handling backend-neutral in the core runtime.
- Moves backend exception normalization into the MySQL, RabbitMQ, Redis, and SQS plugins.
- Raises the MySQL, RabbitMQ, Redis, and SQS plugin package versions to `0.2.0`.

## 1.4.0

- Adds YAML resource registry entry points for external resource plugins.
- Moves Feishu Bitable into the `onestep-feishu-bitable` plugin package.

## 1.3.3

- Replaces Feishu Bitable sink `match_field` with `match_fields` and supports compound-key upsert/update matching.

## 1.3.2

- Adds the Feishu Bitable connector, including incremental sources, table sinks, YAML resources, examples, and tests.

## 1.3.1

- Fixes cancellation cleanup in `wait_for_stop_fetching()` so cancelling a waiter does not leak child tasks.

## 1.2.7

- Adds pure YAML `app.logging.level` support for framework logger control.
- Emits uniform DEBUG-level sink success logs from the shared runtime send path.

## 1.2.61

- Adds `HttpSink` query-parameter support for `GET` and `DELETE` requests.
- Stops sending HTTP request bodies for `GET` and `DELETE` sink methods.
- Extends HTTP sink coverage with focused tests and updates the bundled skill references.

## 1.2.6

- Adds an HTTP sink connector and passthrough YAML tasks for forwarding deliveries to HTTP endpoints.
- Reports Redis stream source topology details to the control plane.
- Adds an onestep worker project skill with scaffold templates and reference documentation.

## 1.2.5

- Preserves IANA schedule timezone names in control-plane reporter payloads when apps rely on `TZ`, keeping cron and interval metadata aligned with deployment-local time.

## 1.2.4

- Extends transient control-plane HTTP failure handling to include `404` responses.

## 1.2.3

- Exposes task-level `notification` payloads on `succeeded` event metadata for control-plane webhook rendering.
- Sanitizes success notification payloads to JSON-safe values without changing sink or hook return-value behavior.

## 1.2.2

- Adds control plane support for manually running tasks once when the source supports manual runs.

## 1.2.1

- Adds RabbitMQ `exclusive` queue support and resource configuration.
- Extends task lifecycle event handling with `STARTED` and `SUCCEEDED`.
- Fixes the reporter metrics/event payload index regression covered by tests.

## 1.2.0

- Adds YAML app definitions with strict validation, named resources, hooks, and
  `handler.ref` wiring.
- Adds `onestep init` to scaffold a minimal standalone YAML worker project.
- Adds `TaskContext.update_current_row()` for mutable current-row deliveries,
  with MySQL table-queue support for in-place row updates.

## 1.0.0

First stable release of the rewritten `onestep` runtime.

- Introduces the `OneStepApp` runtime centered on `Source`, `Sink`, and `Delivery`.
- Ships stable connector support for memory, MySQL, RabbitMQ, SQS, interval, cron, and webhook workloads.
- Includes the `onestep` CLI for `run` and `check`, plus YAML app definitions with `handler.ref` support.
- Includes deployment guidance for `systemd` and separate-process integration with web services.
- Includes `ControlPlaneReporter` support for topology sync, heartbeats, metrics, and runtime events.
- Treats upgrades from `0.5.x` as migrations; see `MIGRATION-0.5-to-1.0.0.md`.

# Changelog

## 1.7.0

- Adds the source/sink resource catalog contract with roles, fields, defaults, secret metadata, connector types, and topology display fields.
- Requires every `ResourceSpecHandler` to provide a matching `ResourceCatalogEntry`; older plugins without catalog metadata are no longer compatible.
- Adds `onestep catalog --json` for exporting the installed resource catalog.
- Raises the MySQL, Postgres, RabbitMQ, Redis, SQS, Kafka, and Feishu Bitable plugin package versions and requires `onestep>=1.7.0`.
- Moves Worker Builder connector/source/sink decisions in the control plane to the installed onestep catalog.

## 1.6.1

- Adds `reporter.service_description` / `ONESTEP_SERVICE_DESCRIPTION` so workers can report service-level descriptions to the control plane.
- Raises the `onestep-control-plane` reporter plugin package to `0.1.1` and requires `onestep>=1.6.1`.

## 1.6.0

- Adds the `onestep build` command for packaging YAML worker projects into deployable zip archives with an `onestep-package.json` manifest.
- Includes conditional `emit.when` predicate modules, pyproject-referenced README/license metadata, and common packaging metadata files in worker packages.
- Resets interval and cron schedule sources after task resume so paused tasks do not backfill stale ticks.
- Keeps control-plane reporter event IDs unique across same-instance restarts.

## 1.5.1

- Moves control-plane reporter and WebSocket command integration into the new `onestep-control-plane` 0.1.0 plugin package.
- Keeps YAML `reporter: true` and legacy control-plane import paths working when `onestep[control-plane]` is installed, with a clear install hint when the plugin is missing.
- Adds the `onestep.reporters` entry-point registry so future reporters can be installed without becoming core dependencies.

## 1.4.7

- Adds task-scoped custom metrics via `ctx.metrics.counter(...).inc()` and `ctx.metrics.gauge(...).set()`, with top-level exports for the custom metric helpers.
- Includes custom metrics in control-plane metrics telemetry when the worker and plane negotiate `telemetry.custom_metrics`.
- Sends an immediate control-plane heartbeat after `pause_task` and `resume_task` commands truly complete, so dashboards can observe task-control state without waiting for the scheduled heartbeat interval.
- Adds a MySQL-backed control-plane demo app target for exercising interval -> handler -> MySQL sink telemetry locally.

## 1.4.6

- Adds true task-level restart for controllable source tasks via `OneStepApp.restart_task_runner()`, cancelling and respawning a single task runner without restarting the whole process.
- Advertises and handles the control-plane `restart_task` WebSocket command, returning the restarted task's fresh control snapshot.
- Keeps per-task restart from closing resources shared by other tasks and adds contract coverage for private and shared task resources.
- Coordinates the new remote command through capability and task-support checks so older runtimes and planes remain forward-compatible.

## 1.4.5

- Adds the `onestep[kafka]` extra for installing the Kafka connector plugin on Python 3.10 and newer.
- Documents the core reliability contract, including stable API tiers, at-least-once delivery semantics, plugin compatibility checks, and core release governance.
- Adds a local reliability check command for core and plugin compatibility verification.

## onestep-kafka 0.1.0

- Adds the `onestep-kafka` plugin package with Kafka topic source and sink support backed by `aiokafka`.
- Registers YAML resource types for `kafka` and `kafka_topic`.
- Uses manual Kafka offset commits with per-partition contiguous ack tracking to preserve onestep at-least-once semantics.
- Adds focused unit tests, runtime shutdown contract tests, package validation, and live Redpanda integration coverage.

## 1.4.4

- Adds `HttpSink` variable replacement for configured URLs, headers, params, and request bodies.
- Adds the YAML `http_sink.body` field for reshaping outbound JSON while keeping default task-result forwarding unchanged.
- Redacts configured HTTP request bodies in control-plane topology descriptors.

## 1.4.3

- Adds YAML conditional sink routing with `when` / `then` / `otherwise` emit entries while preserving legacy fan-out emit behavior.
- Keeps CLI, app description, and control-plane topology output on the existing flattened `emit` sink list.
- Publishes core package releases from GitHub Actions through PyPI Trusted Publishing.

## onestep-postgres 0.1.0

- Adds the `onestep-postgres` plugin package with PostgreSQL table queues, incremental polling sources, table sinks, and SQLAlchemy-backed state/cursor stores.
- Supports YAML resource registration for `postgres`, `postgres_state_store`, `postgres_cursor_store`, `postgres_table_queue`, `postgres_incremental`, and `postgres_table_sink`.
- Adds focused unit tests and live PostgreSQL integration coverage gated by `ONESTEP_POSTGRES_DSN`.

## onestep-mysql 0.3.0

- Adds the `mysql_binlog` source for row-based MySQL binlog CDC.
- Supports YAML and Python wiring for insert/update/delete row events with durable file/position cursor state.
- Adds MySQL binlog examples and live MySQL 8.4 coverage for insert, update, and delete events.

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

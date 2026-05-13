# Changelog

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

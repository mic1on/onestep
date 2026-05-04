# Changelog

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

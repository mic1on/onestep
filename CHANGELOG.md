# Changelog

## 1.0.0

First stable release of the rewritten `onestep` runtime.

- Introduces the `OneStepApp` runtime centered on `Source`, `Sink`, and `Delivery`.
- Ships stable connector support for memory, MySQL, RabbitMQ, SQS, interval, cron, and webhook workloads.
- Includes the `onestep` CLI for `run` and `check`, plus YAML app definitions with `handler.ref` support.
- Includes deployment guidance for `systemd` and separate-process integration with web services.
- Includes `ControlPlaneReporter` support for topology sync, heartbeats, metrics, and runtime events.
- Treats upgrades from `0.5.x` as migrations; see `MIGRATION-0.5-to-1.0.0.md`.

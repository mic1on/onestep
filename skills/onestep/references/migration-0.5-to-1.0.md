# Migration From 0.5.x To 1.x

Use this reference only when the user is upgrading old onestep projects.

## Principle

onestep 1.x is a runtime rewrite. Treat legacy `step` and `*Broker` API upgrades as migrations, not package bumps.

## Migration Approach

1. Inventory old workers, brokers, task names, retry behavior, and deployment commands.
2. Map each legacy broker to a 1.x `Source` and optional `Sink`.
3. Create a minimal `OneStepApp` or YAML `worker.yaml`.
4. Move business logic into Python handlers and transforms.
5. Add only the runtime policy needed to preserve behavior: retry, timeout, concurrency, dead-letter.
6. Validate with `onestep check` and focused tests.

## Typical Mapping

- Legacy task registration -> `OneStepApp.task(...)` or `tasks[]` in YAML.
- Legacy broker consume -> connector source such as `rabbitmq_queue`, `redis_stream`, `sqs_queue`, or `mysql_table_queue`.
- Legacy publish -> connector sink or `emit`.
- Legacy scheduled job -> `IntervalSource.every(...)` or YAML `interval` / `cron`.

## Guardrails

- Do not preserve old abstractions just to make the diff familiar.
- Do not convert business logic into YAML.
- Do not add control-plane reporter during migration unless it is part of the requested rollout.
- Keep old and new workers side-by-side during rollout when queue semantics make that safer.

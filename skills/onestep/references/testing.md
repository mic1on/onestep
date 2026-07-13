# Testing And Validation

Use the narrowest validation that proves the change.

## App And YAML Checks

For Python app targets:

```bash
onestep check your_package.tasks:app
onestep check --json your_package.tasks:app
```

For YAML:

```bash
onestep check worker.yaml
onestep check --strict worker.yaml
```

Use `--strict` for long-lived worker definitions and any YAML generated or modified by AI.

## Focused Unit Tests

When changing runtime behavior in the onestep repo:

```bash
pytest tests/test_cli.py
pytest tests/test_memory_queue.py
pytest tests/test_schedule_sources.py
pytest tests/test_redis_connector.py
pytest tests/test_control_plane_ws.py
```

Choose tests that match the changed module. Do not run integration tests unless required infrastructure is available.

## Core Reliability Checks

When changing onestep core runtime behavior, stable exports, connector plugin
contracts, delivery/retry/dead-letter semantics, or release compatibility
policy, run:

```bash
uv run pytest -q -m "not integration"
./scripts/run-reliability-checks.sh
```

The reliability script runs core tests and each plugin test suite in isolated
pytest processes, avoiding plugin test module-name collisions.

## Integration Tests

Live connector tests usually require external services or credentials:

- RabbitMQ
- Redis
- MySQL
- AWS SQS

Use the repo scripts or docker compose files only when the user asks for integration validation or the task specifically touches live connector behavior.

## Test Shape For User Projects

For YAML worker projects, useful tests usually cover:

- `onestep check --strict worker.yaml`
- handler unit tests with representative payloads
- transform unit tests
- connector integration tests only at the boundary

Keep business logic testable in Python modules instead of burying it in handler bodies.

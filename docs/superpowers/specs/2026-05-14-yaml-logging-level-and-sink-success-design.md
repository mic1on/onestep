# YAML Logging Level And Sink Success Design

## Summary

Add a minimal YAML logging configuration field so pure YAML workers can raise or lower the framework logger level without writing Python hooks. At the same time, add a uniform sink-success debug log at the runtime send layer so successful sink delivery is observable when that logger level is enabled.

This design keeps the scope intentionally small:

- add one new YAML field: `app.logging.level`
- apply it only to the `onestep` logger namespace
- do not add handler, formatter, or per-logger routing config
- do not add sink-specific logging options

## Problem

Today a YAML-defined worker can wire tasks, resources, and hooks, but it cannot directly control the onestep logger level from YAML. That creates an awkward gap for users who want to inspect lower-level framework activity, especially successful sink sends, without introducing a Python startup hook solely to call `logging.getLogger(...).setLevel(...)`.

Separately, successful sink sends do not currently emit a dedicated log. Failures and retryable degradation are visible, but the successful path is silent unless the caller adds custom instrumentation around the task or sink.

## Goals

- Allow pure YAML apps to configure the global onestep logger level.
- Make successful sink sends observable in a uniform way across sink types.
- Preserve existing behavior when no new YAML logging field is set.
- Keep the implementation small and aligned with existing Python logging behavior.

## Non-Goals

- No general Python logging DSL in YAML.
- No root logger configuration.
- No formatter or handler management.
- No per-task or per-logger level overrides in the first version.
- No sink-specific logging fields such as `http_sink.log_level`.

## User-Facing Configuration

Add an optional `logging` mapping under `app`:

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: orders-worker
  logging:
    level: DEBUG
```

Semantics:

- `app.logging.level` is optional.
- Accepted values are standard Python logging names:
  - `DEBUG`
  - `INFO`
  - `WARNING`
  - `ERROR`
  - `CRITICAL`
- Matching is case-insensitive after trimming whitespace.
- If omitted, runtime behavior stays unchanged.

## Logger Scope

The YAML setting only affects the `onestep` logger namespace:

```python
logging.getLogger("onestep").setLevel(resolved_level)
```

This intentionally does not:

- change the root logger level
- add or remove handlers
- change formatter configuration
- override application loggers outside the `onestep` namespace

Rationale:

- The setting is meant to control framework visibility, not take ownership of the host process logging stack.
- `onestep.<app>.<task>` task loggers and related framework loggers already inherit from the `onestep` namespace, so this gives the needed global control with one small setting.

## Sink Success Logging

Add a single debug log on the successful runtime send path in the shared sink dispatch layer rather than in individual connectors.

Target layer:

- `TaskRunner._send_to_sink(...)`

Why this layer:

- it covers all sink implementations uniformly
- it avoids connector-by-connector drift
- it preserves connector implementations for transport behavior instead of generic observability

Proposed log behavior:

- log only after a sink send completes successfully
- level: `DEBUG`
- logger: existing task runner logger for the task, under `onestep.<app>.<task>`

Suggested event fields:

- `sink_name`
- `sink_kind`
- `connector_backend` when available
- `delivery_attempts`

Suggested message:

`"sink send succeeded"`

The exact field set should stay small and be limited to values already cheaply available on the sink object or envelope. The first version should not add heavy payload serialization or HTTP-specific response details.

## Why Not Log Success In Each Connector

Logging success inside `HttpSink.send()` would solve the immediate `http_sink` question, but it would create an inconsistent system:

- `http_sink` would have success logs
- `mysql_table_sink`, `redis_stream`, and others would remain silent
- future sink implementations would need to remember to replicate the pattern

Putting the success log in `TaskRunner._send_to_sink(...)` creates one behavior for all sinks and keeps the model simple:

- task-level success event at `INFO`
- sink-level success detail at `DEBUG`
- sink-level failure or retry at `WARNING` or `ERROR`

## Schema And Validation Changes

The strict YAML schema currently allows these `app` fields:

- `name`
- `shutdown_timeout_s`
- `config`
- `state`

The design extends strict validation to allow:

- `logging`

Within `app.logging`, allow exactly:

- `level`

Validation rules:

- `app.logging` must be a mapping
- `app.logging.level` must be a non-empty string
- normalize and validate against supported logging names
- reject unknown keys under `app.logging`

Legacy top-level app fields remain unchanged. This design only expands the strict `app` section.

## Runtime Application Point

Apply the logger level during YAML app loading, after the `OneStepApp` is created and named, but before tasks start running.

Expected flow:

1. parse YAML
2. validate config
3. create `OneStepApp`
4. if `app.logging.level` is present, resolve it and set `logging.getLogger("onestep").level`
5. continue resource and task wiring

Why here:

- the setting belongs to YAML application bootstrapping
- it should apply before runtime task execution
- it avoids making users depend on hooks for framework boot behavior

## Backward Compatibility

This change is backward compatible.

- Existing YAML files remain valid because `app.logging` is optional.
- Existing Python apps are unaffected unless they choose to load YAML that includes the new field.
- Existing external logging setups continue to work because this design does not modify handlers or the root logger.

The only behavior change after adoption is for users who explicitly set `app.logging.level`. In that case, framework logs under `onestep` become more or less verbose as requested.

## Documentation Changes

Update YAML docs and examples to show:

- the new `app.logging.level` field
- a short explanation that it controls the `onestep` namespace only
- a concrete example where `DEBUG` exposes successful sink send logs

The HTTP sink docs should not imply that HTTP is special here. The logging behavior should be documented as a framework-wide sink dispatch behavior.

## Testing Strategy

Add focused tests for three areas.

### 1. YAML config validation

Cover:

- valid `app.logging.level: DEBUG`
- lowercase input such as `debug`
- invalid type such as integer
- invalid value such as `VERBOSE`
- unknown keys under `app.logging`

### 2. YAML app loading behavior

Cover:

- loading YAML with `app.logging.level: DEBUG` sets the `onestep` logger to `DEBUG`
- loading YAML without the field does not force a new level

Tests should restore logger state after each assertion to avoid cross-test pollution.

### 3. Sink success logging

Cover:

- a task with a sink emits a `DEBUG` log record on successful send
- the log is not emitted at higher logger levels when debug is disabled
- failure-path logging behavior remains unchanged

The sink-success test should prefer an in-memory sink or a minimal fake sink so it stays focused on runtime logging rather than transport behavior.

## Risks And Tradeoffs

### Global namespace scope

Setting `logging.getLogger("onestep").setLevel(...)` affects all descendant framework loggers in the current process, not only one YAML app instance. That is acceptable for the first version because:

- the configuration is explicitly global in scope
- the user asked for a global setting
- it matches the requested pure YAML experience with minimal machinery

If multi-app-in-one-process use becomes important later, a future design can add finer-grained logger scope. That is intentionally out of scope for this version.

### Handler visibility

Changing a logger level does not guarantee the process has a handler that will print debug logs. That is acceptable and should be documented explicitly. This feature controls framework logger verbosity; it does not bootstrap output sinks for Python logging.

### Duplicate high-level success visibility

Tasks already produce a `SUCCEEDED` event at `INFO` when an event logger is attached. Adding sink-success debug logs does not duplicate that signal at the same level because the new logs live at `DEBUG` and carry lower-level connector context.

## Recommended Implementation Boundaries

Expected files to change:

- `src/onestep/config.py`
  - schema expansion
  - parsing and validation helpers
  - logger-level application during YAML app load
- `src/onestep/runtime/runner.py`
  - sink-success debug log
- `docs/yaml-task-definition.md`
  - user-facing YAML documentation
- `tests/test_cli.py` or a focused YAML config test module
  - YAML validation and load behavior
- `tests/contract/test_runtime_contract.py` or a focused runtime logging test module
  - sink-success log behavior

This design does not require changes in:

- `src/onestep/connectors/http.py`
- `src/onestep/context.py`
- sink-specific resource schemas

## Recommendation

Proceed with the smallest complete version:

1. add `app.logging.level`
2. validate and apply it to the `onestep` namespace during YAML load
3. add one shared sink-success debug log in `TaskRunner._send_to_sink(...)`
4. document the new YAML field and the expected debug visibility behavior

That solves the pure YAML use case cleanly without growing a parallel logging configuration language.

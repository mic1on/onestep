# CLI-Managed Logging Design

## Summary

Make `onestep run` provide useful process logging and task lifecycle logs without
requiring every application module to configure Python logging or register a
`StructuredEventLogger`.

The CLI will:

- configure a standard stdout handler when the host process has not configured logging
- resolve the framework log level from an explicit CLI option, YAML configuration, or an INFO default
- register a `StructuredEventLogger` by default
- avoid replacing existing logging handlers or duplicating an existing structured event logger
- allow operators to disable task event logging

Direct library usage such as `app.run()` or `await app.serve()` remains unchanged.

## Problem

Python applications currently repeat process bootstrap code similar to:

```python
import logging
import sys

from onestep import StructuredEventLogger

event_logger = StructuredEventLogger(
    logger=logging.getLogger("onestep.kpi_sync.events")
)


def configure_logging() -> None:
    level = logging.getLogger("onestep").getEffectiveLevel()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
        force=True,
    )


app.on_event(event_logger)
```

This mixes three responsibilities in every app:

- business code obtains named loggers
- the task runtime converts lifecycle events to log records
- the process entrypoint configures handlers, formatting, level, and output stream

The first responsibility belongs in application code. The second belongs to the
runtime integration, and the third belongs to the CLI process entrypoint. Repeating
the latter two makes apps noisy and encourages `force=True`, which can unexpectedly
remove handlers installed by a host, test runner, or monitoring integration.

## Goals

- Make `onestep run package.module:app` produce useful logs without app-level bootstrap code.
- Emit INFO-level task success events and WARNING/ERROR lifecycle events by default.
- Make DEBUG framework and lifecycle records available through one CLI option.
- Preserve logging configuration installed while importing the target application.
- Keep custom event hooks and custom logging configurations supported.
- Keep YAML `app.logging.level` behavior compatible.

## Non-Goals

- Do not create a general logging configuration DSL.
- Do not add JSON logging, file logging, rotation, or per-task level controls.
- Do not change `StructuredEventLogger` event fields or event level mappings.
- Do not change reporter payloads, task event names, or control-plane behavior.
- Do not configure logging for direct `OneStepApp.run()` or `OneStepApp.serve()` callers.
- Do not automatically convert arbitrary application logger names into the `onestep` namespace.

## User Experience

### Minimal Python Application

An application only creates a business logger and defines its runtime:

```python
import logging

from onestep import OneStepApp

app = OneStepApp("kpi-sync")
logger = logging.getLogger("onestep.kpi_sync")


@app.task(source=source)
async def sync_kpi(ctx, item):
    logger.info("starting KPI sync")
```

The standard command provides process and task event logging:

```bash
onestep run kpi_sync.app:app
```

The application no longer imports `sys`, constructs a `StructuredEventLogger`,
calls `logging.basicConfig()`, or registers the standard event logger.

Applications still import `TaskEvent` when they implement custom event behavior,
such as sending a failure notification:

```python
from onestep import TaskEvent, TaskEventKind


@app.on_event
async def notify_failure(app, event: TaskEvent):
    if event.kind is TaskEventKind.FAILED:
        await send_alert(event)
```

### CLI Options

`onestep run` adds:

```text
--log-level {DEBUG,INFO,WARNING,ERROR,CRITICAL}
--task-events / --no-task-events
```

Examples:

```bash
onestep run kpi_sync.app:app --log-level DEBUG
onestep run kpi_sync.app:app --no-task-events
```

`--task-events` is enabled by default. The positive form is accepted so generated
commands can state the desired behavior explicitly.

The options apply only to `run`. They are not accepted by `check`, because checking
a target should not install runtime observers or process logging.

## Log Level Resolution

The effective `onestep` namespace level uses this precedence:

1. an explicit `onestep run --log-level LEVEL`
2. a level explicitly configured while loading the target, including YAML `app.logging.level`
3. the CLI default, `INFO`

For a Python target that does not set an `onestep` logger level, the default is INFO
unless the CLI option is present. A Python target that deliberately sets the namespace
level during import keeps that value, just as a YAML target does.

The CLI must distinguish an omitted `--log-level` from an explicit value. It must
not blindly apply INFO after loading an app, because that would overwrite YAML or
Python target logging configuration.

The resolved level is applied to the `onestep` logger namespace. Applications that
want their business logs governed by the same setting should use a descendant name,
such as `onestep.kpi_sync` or `onestep.<app-name>.business`.

## Process Logging Configuration

Logging configuration occurs after the target is loaded and before `app.run()`.
Loading first gives application modules a chance to install their own logging setup.

If the root logger has no handlers, the CLI adds one `logging.StreamHandler` targeting
`sys.stdout` with this format:

```text
%(asctime)s %(levelname)s %(name)s %(message)s
```

The CLI does not call `logging.basicConfig(force=True)`. It does not remove, replace,
or reformat existing root or named logger handlers.

When an existing handler is present, the CLI only applies the resolved level to the
`onestep` namespace. Handler levels and formatters remain owned by the application or
host process. This prevents the CLI from silently weakening an explicitly configured
handler policy.

The CLI removes and closes a handler it installed after `app.run()` returns. This
does not affect normal worker output because the handler remains active for the full
runtime, and it keeps repeated in-process CLI invocations from retaining duplicate or
stale stream handlers. Handlers owned by the application or host are never removed.

## Task Event Registration

Before calling `app.run()`, the CLI registers `StructuredEventLogger()` when task
events are enabled.

If the application has already registered any `StructuredEventLogger`, the CLI does
not add another one. The existing instance wins, including its logger name and custom
`level_by_kind` mapping. Other event handlers do not suppress the default structured
event logger.

To avoid CLI code reaching into `OneStepApp._event_handlers`, `OneStepApp` will expose
a small idempotent helper:

```python
app.enable_structured_event_logging() -> StructuredEventLogger
```

The helper returns the existing structured logger when present, otherwise creates,
registers, and returns a default instance. Applications may use it directly, but the
primary intended caller is `onestep run`.

When `--no-task-events` is present, the CLI does not call the helper. It does not
remove an event logger explicitly registered by application code; explicit app
configuration remains authoritative.

## Compatibility

### Existing CLI Applications

Existing apps with no logging setup gain INFO-level stdout logs and lifecycle events.
This is the intended behavior change.

Existing apps that configure root handlers keep those handlers and formatters. The CLI
does not add a second root handler.

Existing apps that register a custom `StructuredEventLogger` keep it without duplicate
event output.

### Direct Runtime Embedding

Calling `app.run()` or `await app.serve()` directly does not configure handlers or add
event observers. Host applications retain complete ownership of process logging.

### YAML Applications

YAML `app.logging.level` retains its current meaning. It controls the `onestep`
namespace when no explicit CLI level is supplied. The CLI supplies stdout output and
the standard task event observer for `run` just as it does for Python targets.

### Custom Event Hooks

Custom `@app.on_event` hooks continue to run alongside the CLI-managed structured
logger. `--no-task-events` disables only the CLI's automatic registration; it does not
disable application-defined hooks.

## Failure Handling

- `--log-level` is constrained by argparse choices, producing a standard usage error
  for unsupported values.
- Logging setup failures are treated as CLI startup failures and use the existing
  `onestep: ... failed while running` error path where practical.
- A logging handler or event hook failure during runtime retains existing Python
  logging and `OneStepApp.emit_event()` behavior.
- No payload data is added to standard log messages, avoiding accidental disclosure
  of task bodies.

## Documentation Changes

- Document the new `run` options in the English and Chinese READMEs.
- Explain that application modules should not call `basicConfig(force=True)` when run
  through the CLI.
- Update the CLI example to use the minimal application pattern.
- Keep the runtime showcase's custom JSON formatter because it demonstrates an
  intentional custom logging setup rather than required boilerplate.
- Add a changelog entry under the new core package version prepared for shipping.

## Testing

Focused CLI tests will cover:

- parsing valid and invalid `--log-level` values
- default INFO level for Python targets
- explicit CLI level override
- YAML logging level preservation when the option is omitted
- explicit CLI level precedence over YAML
- stdout handler and standard formatter installation when no handler exists
- preservation of existing root handlers and formatters
- default structured event logger registration
- `--no-task-events`
- no duplicate registration when the app already has a `StructuredEventLogger`
- no removal of an app-registered logger when `--no-task-events` is used
- no logging mutation for `check`

Runtime contract tests will cover the idempotent
`OneStepApp.enable_structured_event_logging()` helper.

The full core test suite and package checks will run before the pull request is opened.

## Release And Coordination

This is a core package feature, so shipping it requires:

- bumping the root `pyproject.toml` core version
- updating `uv.lock`
- updating `CHANGELOG.md`
- creating the matching annotated `v<version>` tag only when the package is actually released

Opening the implementation pull request does not itself publish the package or create
the release tag.

No control-plane coordination is required. The change does not alter reporter payloads,
task lifecycle event names or semantics, WebSocket behavior, runtime identity, or
remote task control.

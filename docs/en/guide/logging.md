---
title: Logging & Task Events | Guide
outline: deep
---

# Logging & Task Events

Since onestep 1.7.2, `onestep run` provides default logging configuration for standalone processes. Applications no longer need to call `logging.basicConfig(force=True)` or manually register `StructuredEventLogger` for regular task events.

## Application Code

Applications continue to use the Python standard library logger. The logger name is determined by the application and does not need to start with `onestep`:

```python
import logging

from onestep import IntervalSource, OneStepApp

app = OneStepApp("billing-sync")
logger = logging.getLogger("billing.kpi_sync")


@app.task(source=IntervalSource.every(hours=1, immediate=True))
async def sync_billing(ctx, _):
    logger.info("sync started")
```

Start with the CLI:

```bash
onestep run your_package.tasks:app
```

By default, INFO-level and above application logs and task lifecycle events are written to stdout.

## Log Level

```bash
onestep run your_package.tasks:app --log-level DEBUG
onestep run your_package.tasks:app --log-level WARNING
```

The level is resolved with the following priority:

1. Explicitly passed `--log-level`
2. Any level already configured when the target is loaded, including YAML `app.logging.level`
3. Default `INFO`

DEBUG includes details like fetched, started, and sink-success. INFO primarily records application logs along with task results such as succeeded, retried, failed, dead-lettered, and cancelled.

## Task Event Toggle

The CLI enables `StructuredEventLogger` by default. You can disable it when task lifecycle logs aren't needed:

```bash
onestep run your_package.tasks:app --no-task-events
```

If the application has already registered a `StructuredEventLogger`, the CLI reuses the existing instance and does not duplicate output. Other custom `@app.on_event` handlers are unaffected.

## Coexisting with Host Logging

The CLI loads the target application first, then decides whether to configure logging:

- When the root logger has no handlers, the CLI adds a stdout handler and sets the corresponding root level during the run.
- When the root logger already has handlers, the CLI does not replace handlers, formatters, or the root level; the logging strategy remains the host's responsibility.
- Handlers and root level added by the CLI are restored after the run succeeds or fails.

This means onestep won't override host settings when Gunicorn, a test framework, or a platform launcher has already configured logging.

## Embedded Runs

Calling `app.run()` or `app.serve()` directly does not modify process logging or automatically register task events. Embedded applications can configure themselves:

```python
import logging

logging.basicConfig(level=logging.INFO)
app.enable_structured_event_logging()
app.run()
```

`enable_structured_event_logging()` is idempotent. If the application needs a separate event logger, you can register one explicitly:

```python
import logging

from onestep import StructuredEventLogger

app.on_event(
    StructuredEventLogger(logger=logging.getLogger("billing.task_events"))
)
```

## YAML Applications

YAML can specify the target log level:

```yaml
app:
  name: billing-sync
  logging:
    level: WARNING
```

`onestep run worker.yaml --log-level DEBUG` overrides the YAML value. When `--log-level` is not passed, the YAML configuration is preserved. See [YAML Task Definition](/en/yaml-task-definition) for other YAML logging rules.

## Next Steps

- [Events & Lifecycle](/en/core/middleware) - task events and custom handlers
- [Production Deploy](/en/guide/deploy) - systemd, containers, and YAML deployment
- [YAML Task Definition](/en/yaml-task-definition) - YAML log level configuration

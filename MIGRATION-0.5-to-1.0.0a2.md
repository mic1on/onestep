# Migration Guide: 0.5.x to 1.0.0a2

This guide is for projects built on the legacy `0.5.x` API surface:

- `step`
- `*Broker`
- `step.start(...)`
- legacy retry, middleware, worker, and group options

`1.0.0a2` is not a drop-in upgrade. The package now exposes a new async runtime
based on `OneStepApp`, `Source`, `Sink`, and `Delivery`. Treat this as a
migration.

## Before you upgrade

Upgrade now if all or most of the following are true:

- your project uses RabbitMQ, SQS, MySQL, webhook, cron, or in-memory flows
- you can rewrite task registration and startup code
- you are willing to adopt the new CLI and deployment model

Delay the upgrade if one of these is true:

- your project depends on `RedisBroker`, `RedisStreamBroker`, or `RedisPubSubBroker`
- your project relies heavily on `middleware`, `worker_class`, `group`, or
  `error_callback`
- you need full backward compatibility with the `step` decorator model

## High-impact changes

The upgrade changes the programming model, runtime, and deployment entrypoint.

| Area | 0.5.x | 1.0.0a2 |
| --- | --- | --- |
| App definition | `@step(...)` | `app = OneStepApp(...)` + `@app.task(...)` |
| Input backend | `from_broker=` | `source=` |
| Output backend | `to_broker=` | `emit=` |
| Concurrency | `workers=` | `concurrency=` |
| Startup | `step.start(...)` | `app.run()` or `onestep run package.module:app` |
| CLI target | `onestep example_module` | `onestep run package.module:app` |
| Retry model | `TimesRetry`, `NeverRetry`, `AdvancedRetry`, etc. | `NoRetry`, `MaxAttempts`, or custom `RetryPolicy` |
| Runtime events | middleware/error callback oriented | lifecycle hooks and event handlers |

## Broker and connector mapping

These legacy brokers have a direct or near-direct migration path:

| 0.5.x | 1.0.0a2 |
| --- | --- |
| `MemoryBroker` | `MemoryQueue` |
| `CronBroker` | `CronSource(...)` or `IntervalSource.every(...)` |
| `WebHookBroker` | `WebhookSource(...)` |
| `RabbitMQBroker` | `RabbitMQConnector(...).queue(...)` |
| `SQSBroker` | `SQSConnector(...).queue(...)` |
| `MysqlBroker` | `MySQLConnector.table_queue(...)`, `table_sink(...)`, or `incremental(...)` |

These legacy brokers do not currently have a v1 replacement in this repo:

- `RedisBroker`
- `RedisStreamBroker`
- `RedisPubSubBroker`

If your production path depends on those brokers, stay on `0.5.x` until you are
ready to replace that backend yourself.

## Removed or redesigned legacy concepts

The following legacy interfaces should be treated as removed without a direct
compatibility layer:

- `step`
- `BaseBroker` / `BaseConsumer`
- `BaseMiddleware` and config middleware variants
- `worker_class`
- `group`
- `BaseErrorCallback` and `NackErrorCallBack`
- `NeverRetry`, `AlwaysRetry`, `TimesRetry`, `RetryIfException`, `AdvancedRetry`

What to do instead:

- move task registration onto a `OneStepApp`
- use `@app.on_startup` and `@app.on_shutdown` for lifecycle work
- use `@app.on_event` for event observation and metrics hooks
- rewrite retry behavior with `NoRetry`, `MaxAttempts`, or a custom
  `RetryPolicy`
- redesign middleware-style logic explicitly in task handlers, connector setup,
  or event hooks

## Minimal before/after example

Old `0.5.x` style:

```python
from onestep import WebHookBroker, step


@step(from_broker=WebHookBroker(path="/push"))
def waiting_messages(message):
    print("received:", message.body)


if __name__ == "__main__":
    step.start(block=True)
```

New `1.0.0a2` style:

```python
from onestep import OneStepApp, WebhookSource

app = OneStepApp("webhook-demo")


@app.task(source=WebhookSource(path="/push", host="127.0.0.1", port=8080))
async def waiting_messages(ctx, payload):
    print("received:", payload["body"])


if __name__ == "__main__":
    app.run()
```

The preferred production entrypoint is still the CLI:

```bash
onestep run your_package.tasks:app
```

## Suggested migration sequence

Use this order for an existing project:

1. Inventory the legacy surface.
   Search for `@step`, `step.start`, broker classes, retry classes, middleware,
   and custom worker logic.
2. Confirm your supported backends exist in v1.
   If the project depends on Redis brokers, stop here and defer the upgrade.
3. Extract business logic from decorated functions.
   Keep the core processing code separate from the old broker wrappers.
4. Create a dedicated app module.
   Define `app = OneStepApp("your-app")` in a module such as
   `your_project/onestep_app.py`.
5. Rebuild task registration.
   Replace each `@step(...)` with `@app.task(...)`, mapping `from_broker` to
   `source`, `to_broker` to `emit`, and `workers` to `concurrency`.
6. Rebuild retry and error handling.
   Replace legacy retry classes and callbacks with `NoRetry`, `MaxAttempts`, or
   a custom `RetryPolicy`.
7. Switch startup and deployment.
   Replace `step.start(...)` and old CLI usage with `onestep run ...`.
8. Revisit deployment shape.
   For web framework projects, run the web app and OneStep as separate
   processes. See `deploy/web-service-integration.md`.

## Environment and dependency changes

Check these before rollout:

- Python `>=3.9` is now required
- optional extras have changed:
  - `.[mysql]`
  - `.[rabbitmq]`
  - `.[sqs]`
  - `.[all]`
- legacy package integrations such as `use-rabbitmq`, `use-redis`, and
  `use-sqs` are not part of the current v1 dependency model

## Rollout guidance for old projects

For a production migration, prefer a side-by-side rollout:

- keep the `0.5.x` worker running while you build the v1 app in a separate module
- validate the new task behavior against the same payload shapes and backend data
- cut traffic to the v1 worker only after connector behavior, retries, and
  shutdown semantics are verified

If your project already includes FastAPI or Django, do not auto-start OneStep
inside the web worker by default. Use separate processes and a shared backend
queue. The deployment recommendation is documented in
`deploy/web-service-integration.md`.

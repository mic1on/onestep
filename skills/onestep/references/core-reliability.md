# Core Reliability

Use this reference when modifying onestep core runtime code, connector plugin
contracts, resource registration, delivery/retry/dead-letter behavior,
control-plane reporter behavior, or release/version policy.

## Stable API Boundaries

Stable application API imported from `onestep`:

- `OneStepApp`, `TaskContext`
- `Source`, `Sink`, `Delivery`, `Envelope`
- `MemoryQueue`, `IntervalSource`, `CronSource`, `WebhookSource`, `HttpSink`
- `NoRetry`, `MaxAttempts`, `RetryPolicy`, `RetryAction`, `RetryDecision`
- `TaskEvent`, `TaskEventKind`, `InMemoryMetrics`, `StructuredEventLogger`

Stable plugin API:

- `ResourceRegistry`, `ResourceSpecHandler`
- `ResourceBuildContext`, `ResourceValidationContext`
- `register_resource_type`, `load_resource_plugins`
- `ConnectorOperationError`, `ConnectorOperation`, `ConnectorErrorKind`

Operational API:

- `ControlPlaneReporter`, `ControlPlaneReporterConfig` when `onestep[control-plane]` is installed
- exported identity helpers
- exported low-level WebSocket transport classes when `onestep[control-plane]` is installed

Do not remove, rename, or change the meaning of stable exported names without
treating it as a compatibility change.

## Delivery Semantics

onestep is at-least-once, not exactly-once.

Successful delivery order:

1. `delivery.start_processing()`
2. handler
3. success hooks
4. selected sink sends
5. `delivery.ack()`
6. success event

Important consequences:

- A sink can receive output before `ack()` fails.
- A crash after a sink send and before `ack()` can duplicate downstream output.
- Multi-sink fan-out is not transactional; earlier successful sends are not
  rolled back if a later sink fails.
- Production handlers and sinks should be idempotent when duplicate output
  matters.

Failure behavior:

- Handler, predicate, hook, sink, and timeout failures enter the existing
  retry/dead-letter path.
- Dead-letter publish failure retries the original delivery.
- Delivery `fail()` failure falls back to retrying the original delivery.
- Cancellation retries the delivery.

## Stop Controls

- `drain` stops new fetches and waits for inflight deliveries.
- `pause_task` stops new fetches for one task and waits for that task's
  inflight deliveries.
- `resume_task` lets a paused task fetch again.
- Shutdown waits for inflight work up to `shutdown_timeout_s`.
- Sources that cannot safely cancel `fetch()` should set
  `fetch_is_cancel_safe = False`; deliveries claimed but not processed must
  support `release_unstarted()`.

## YAML And Plugin Contracts

- Strict YAML mode rejects unknown fields.
- Resource handlers own strict validation for plugin resource fields.
- `app.strict_env` catches missing env variables without defaults.
- YAML stays a wiring layer, not a workflow DSL.
- Plugin resources register through the `onestep.resources` entry point group.

## Validation

For core runtime or plugin contract changes, run:

```bash
uv run pytest -q -m "not integration"
./scripts/run-reliability-checks.sh
```

For narrow changes, run focused tests first, then the reliability command before
release or handoff.

## Release Checklist

Before releasing core, answer:

- Did stable application APIs change?
- Did stable plugin APIs change?
- Did delivery, retry, dead-letter, drain, pause, resume, shutdown, or
  cancellation semantics change?
- Did reporter payloads, topology fields, lifecycle event names/semantics,
  runtime identity, remote-control behavior, or WebSocket behavior change?
- Do plugins need lower-bound dependency updates such as `onestep>=<version>`?
- Does `CHANGELOG.md` describe compatibility impact?
- Does `onestep-control-plane` need coordination?

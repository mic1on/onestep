# Core Reliability Contract

## Status

`onestep` core provides an at-least-once async task runtime. It is intended to
be stable for application code and connector plugins, but only the APIs listed
in this document should be treated as compatibility commitments.

## Stable Application API

- `OneStepApp`
- `TaskContext`
- `Source`, `Sink`, `Delivery`
- `Envelope`
- `MemoryQueue`, `IntervalSource`, `CronSource`, `WebhookSource`, `HttpSink`
- `NoRetry`, `MaxAttempts`, `RetryPolicy`, `RetryAction`, `RetryDecision`
- `TaskEvent`, `TaskEventKind`, `InMemoryMetrics`, `StructuredEventLogger`

Application code may import these names from `onestep`.

## Stable Plugin API

- `ResourceRegistry`
- `ResourceSpecHandler`
- `ResourceBuildContext`
- `ResourceValidationContext`
- `register_resource_type`
- `load_resource_plugins`
- `ConnectorOperationError`, `ConnectorOperation`, `ConnectorErrorKind`

Connector packages may rely on these names when implementing resource handlers
or normalizing backend failures.

## Operational API

- `ControlPlaneReporter` when `onestep[control-plane]` is installed
- `ControlPlaneReporterConfig` when `onestep[control-plane]` is installed
- identity helpers exported from `onestep`
- low-level WebSocket transport classes when `onestep[control-plane]` is installed

These APIs are supported for operational integration, but changes may require
coordination with `onestep-control-plane`.

## Internal API

Anything not listed above should be treated as internal even when it can be
imported by module path.

## Delivery Semantics

`onestep` is at-least-once. It does not provide exactly-once delivery.

For a successful delivery, the runtime:

1. calls `delivery.start_processing()`
2. invokes the task handler
3. runs success hooks
4. sends the handler result to selected sinks
5. calls `delivery.ack()`
6. emits a success event

Because sink sends happen before `ack()`, a process crash or `ack()` failure
after a successful sink send can produce duplicate downstream output.
Production handlers and sinks should be idempotent when duplicate output
matters.

Multi-sink fan-out is not transactional. If an earlier sink succeeds and a
later sink fails, the earlier send is not rolled back. A retry may send to the
earlier sink again.

## Failure Semantics

- Handler, predicate, hook, sink, and timeout failures enter the task
  retry/dead-letter path.
- Retry policies decide whether the delivery is retried or failed.
- Dead-letter publish failure retries the original delivery.
- Delivery `fail()` failure falls back to retrying the original delivery.
- Cancellation retries the delivery.

## Stop Controls

- `drain` stops new fetches and waits for inflight deliveries to finish.
- `pause_task` stops new fetches for one task and waits for that task's inflight
  deliveries.
- `resume_task` lets a paused task fetch again.
- Shutdown waits for inflight work up to `shutdown_timeout_s`.
- Sources that cannot safely cancel a fetch should set
  `fetch_is_cancel_safe = False` and implement `release_unstarted()` on
  deliveries that have been claimed but not processed.

## YAML And Resource Contracts

- Strict YAML mode rejects unknown fields.
- Resource handlers own strict field validation for plugin resource types.
- `app.strict_env` catches missing environment variables without defaults.
- YAML remains a wiring layer, not a workflow DSL.
- Plugin resources register through the `onestep.resources` entry point group.

## Local Reliability Verification

Run core non-integration tests plus every plugin suite in isolated pytest
processes:

```bash
./scripts/run-reliability-checks.sh
```

This avoids pytest module-name collisions between plugin test suites while
preserving the same coverage expected from local core changes.

## Release Checklist

Before releasing core, answer:

- Does this change affect stable application APIs?
- Does this change affect stable plugin APIs?
- Does this change alter delivery, retry, dead-letter, drain, pause, resume,
  shutdown, or cancellation semantics?
- Does this change alter reporter payloads, topology fields, task lifecycle
  events, runtime identity, remote-control behavior, or WebSocket protocol
  behavior?
- Do any plugins need a lower-bound dependency update such as
  `onestep>=<new-version>`?
- Does `CHANGELOG.md` describe the user-visible behavior and compatibility
  impact?
- Does `onestep-control-plane` need coordination?

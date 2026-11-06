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
- `ExecutionClient`, execution snapshots, execution backend protocols, and
  managed completion protocol
- `MemoryQueue`, `IntervalSource`, `CronSource`, `WebhookSource`, `HttpSink`
- `NoRetry`, `MaxAttempts`, `RetryPolicy`, `RetryAction`, `RetryDecision`
- `TaskEvent`, `TaskEventKind`, `InMemoryMetrics`, `StructuredEventLogger`

Application code may import these names from `onestep`.

`ExecutionBackend` is the client-side protocol for submit, query, and cancel.
Worker-facing leased sources use `LeasedExecutionBackend`, which adds claim,
heartbeat, complete, release, and lease-remaining operations. Lease records and
heartbeat results are `ExecutionLease` and `HeartbeatResult`; a backend may use
its own concrete subclasses or structurally compatible records.

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

These APIs are supported for operational integration. Changes may require a
same-PR update to `apps/control-plane` and must pass the control-plane contract workflow.

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

For a delivery implementing the optional managed execution protocol, the
runtime keeps the same handler, hook, sink, and event ordering but replaces the
final `ack()` call with `complete_execution()`. That completion carries the
handler result, retry delay, or public error to the execution backend. The
ordinary `Delivery.ack()` API has no result argument; a direct legacy `ack()`
therefore cannot persist a handler result and records the connector-specific
success value defined by that delivery.

Because sink sends happen before `ack()`, a process crash or `ack()` failure
after a successful sink send can produce duplicate downstream output.
Production handlers and sinks should be idempotent when duplicate output
matters.

Multi-sink fan-out is not transactional. If an earlier sink succeeds and a
later sink fails, the earlier send is not rolled back. A retry may send to the
earlier sink again. When a fan-out emit partially fails, the execution failure
names exactly which sinks succeeded (`sinks_succeeded`), making the retry
replay window — which sinks will see the row again — explicit.

## Attempt Counting And Retry Budgets

`Envelope.attempts` is 0-based: `0` is the first delivery. `MaxAttempts` and
exponential backoff read this counter. Whether the counter survives process
restarts or ownership changes is a per-source property:

| Source | Counter persistence |
| --- | --- |
| Memory queue | Process-local. Returned unstarted deliveries keep their count and are redelivered first, in return order; `size()` may temporarily exceed `maxsize` while stopping. |
| SQL table queue (`onestep-sql` ≥ 0.7.2) | Per row, in the database. `retry()` increments before applying `nack`; `release_unstarted()` applies `nack` without incrementing; `ack()` / `complete()` clear it. Terminal `fail()` keeps the exhausted count while the row still matches `where`, so the row continues down the failure/DLQ path instead of getting a fresh retry budget. |
| MySQL binlog CDC (`onestep-sql` ≥ 0.7.2) | Local redelivery queue guarded by the cursor barrier; counts reset on restart, which replays unacknowledged events from the persisted cursor. See [SQL](/broker/sql#确认与恢复). |
| Redis Streams (`onestep-redis` ≥ 0.2.3) | Stored in the PEL, preserved across worker restarts: `attempts = encoded_attempts + times_delivered - 1`. Expired entries from any consumer (including dead processes) are reclaimed by a bounded `XAUTOCLAIM` scan (`reclaim_idle_ms`, default 60000); active deliveries renew every 1/3 of the interval. ACK, retry, and lease renewal atomically fence on the PEL consumer and delivery count. |
| Kafka (`onestep-kafka` ≥ 0.1.4) | Not persisted by the broker. The local retry budget applies per continuous partition assignment and resets on restart or rebalance; cross-disconnect bounds need a broker-side dead-letter policy. Commits follow the completion prefix of actually fetched records. |
| MongoDB (`onestep-mongodb` ≥ 0.1.3) | Per source instance, keyed by cursor / resume token. Generation invalidation and replay preserve counts; releasing unstarted deliveries does not consume attempts. A new process starts from zero at the persisted cursor. |
| AWS SQS (`onestep-sqs` ≥ 0.2.5) | Uses real broker delivery history: `attempts = max(0, ApproximateReceiveCount - 1)` on top of the publisher-encoded count. SQS's 1-based receive count survives consumer restarts and includes visibility expiries and unstarted releases. Terminal actions still follow `on_fail` (`delete` or broker redrive when the message must leave the source). |
| Cloudflare Queues (`onestep-cf-queues` ≥ 0.2.1) | Same pattern via the Pull API's 1-based `message.attempts`: `attempts = max(0, message.attempts - 1)`, preserved across consumer restarts; visibility expiry and explicit releases also consume broker deliveries. |
| Feishu Bitable (`onestep-feishu-bitable` ≥ 0.6.2) | Per source instance. Retries reuse the already-fetched payload and increment `attempts`; released unstarted deliveries do not consume attempts; a failed or released row blocks the durable cursor from advancing past it even when later rows succeed. A restart rereads from the persisted cursor with counts reset. |

In every case, a restart can re-deliver rows that already succeeded, so
handlers and sinks must be idempotent; `MaxAttempts` bounds retries within the
documented lifetime of the counter.

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
- Does `apps/control-plane` need a same-PR update and contract validation?

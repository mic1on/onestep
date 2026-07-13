# onestep Core Reliability Design

## Summary

`onestep` core is already a credible runtime kernel for plugin-based data movement. The current design has the right center of gravity: a small dependency-free core package, connector packages registered through resource entry points, and a runtime built around `Source`, `Sink`, `Delivery`, and `OneStepApp`.

This design does not propose a runtime rewrite. It turns the current implicit reliability model into an explicit dependency contract so plugin authors and production users can rely on the core package with fewer surprises.

The recommended direction is contract-first hardening:

1. define the stable public API surface
2. document runtime delivery semantics
3. add plugin compatibility gates
4. tighten release governance around core and plugin version coupling

## Current Assessment

The core package is close to production-grade as a dependency:

- `pyproject.toml` declares no required runtime dependencies for the core package.
- Optional features are split into extras such as `yaml`, `control-plane`, and connector extras.
- Connectors implement a small contract through `Source`, `Sink`, and `Delivery`.
- The runtime handles concurrency, retries, dead letters, timeout classification, drain, pause, resume, manual runs, and control-plane telemetry.
- The main non-integration test suite passes locally: `301 passed`.
- Each plugin test suite passes when run independently.
- CI runs non-integration tests across Python 3.9 through 3.12 and has a live integration job.

The remaining reliability risk is not that the runtime lacks core mechanics. The risk is that some mechanics are only implied by code and tests. For a package advertised as stable, implicit behavior becomes accidental API.

## Problem

As the core package of the onestep plugin ecosystem, `onestep` needs a clearer boundary between:

- stable APIs plugin authors may depend on
- runtime semantics users must design around
- internal details that may change between releases

Four specific gaps should be addressed.

First, the public API is broad. `onestep.__init__` exports runtime classes, resource registry helpers, retry classes, control-plane WebSocket types, identity helpers, and connector primitives. Users will naturally treat these exports as compatibility commitments.

Second, delivery semantics are at-least-once but not stated as a first-class contract. The runtime sends handler output to selected sinks before acknowledging the source delivery. If an ack fails or the process exits after a sink send, downstream consumers may observe duplicates. This is acceptable for a worker runtime, but it must be documented plainly.

Third, plugin compatibility is mostly protected by per-plugin tests, but a single-process run of all plugin tests currently hits pytest import-name conflicts because some plugin test files share basenames. CI avoids this by running plugins separately. That is workable, but it weakens local confidence when changing core behavior.

Fourth, release governance exists in practice but is not fully encoded as a core dependency policy. Core changes that affect plugin contracts, reporter payloads, resource schemas, or control-plane semantics need an explicit checklist.

## Goals

- Make the stable public API obvious to users and plugin authors.
- Make delivery, retry, dead-letter, drain, pause, cancel, and sink failure semantics explicit.
- Preserve the current lightweight plugin architecture.
- Add focused tests where a documented contract is not already covered.
- Make it easy to verify that a core change does not break existing plugins.
- Keep implementation changes surgical.

## Non-Goals

- No major runtime rewrite.
- No exactly-once delivery guarantee.
- No new workflow DSL.
- No connector SDK split into a separate package.
- No broad refactor of `OneStepApp`, `TaskRunner`, or YAML loading.
- No control-plane protocol expansion unless a later implementation plan identifies a specific compatibility gap.

## Reliability Contract

### Stable API Tiers

The core package should define three API tiers.

Stable application API:

- `OneStepApp`
- `TaskContext`
- `Source`, `Sink`, `Delivery`
- `Envelope`
- built-in core resources such as `MemoryQueue`, `IntervalSource`, `CronSource`, `WebhookSource`, and `HttpSink`
- retry types such as `NoRetry`, `MaxAttempts`, `RetryPolicy`, `RetryAction`, and `RetryDecision`
- event and metrics types such as `TaskEvent`, `TaskEventKind`, `InMemoryMetrics`, and `StructuredEventLogger`

Stable plugin API:

- `ResourceRegistry`
- `ResourceSpecHandler`
- `ResourceBuildContext`
- `ResourceValidationContext`
- `register_resource_type`
- `load_resource_plugins`
- connector resilience classes such as `ConnectorOperationError`, `ConnectorOperation`, and `ConnectorErrorKind`

Operational API:

- `ControlPlaneReporter`
- `ControlPlaneReporterConfig`
- identity helpers used by deployments
- WebSocket transport classes only if the project chooses to support direct low-level use

Anything not classified should be considered internal even if importable by module path. The implementation plan should decide whether to document this in README, a dedicated API policy doc, or both.

### Delivery Semantics

The runtime should explicitly promise at-least-once processing, not exactly-once processing.

For a successful delivery:

1. the runtime calls `delivery.start_processing()`
2. the handler runs
3. success hooks run
4. selected sinks receive the result envelope
5. the runtime calls `delivery.ack()`
6. a success event is emitted

Consequences:

- If a sink send succeeds but `ack()` fails, the delivery may be retried and downstream sinks may receive duplicates.
- If multiple sinks are configured and a later sink fails, earlier successful sink sends are not rolled back.
- Production handlers and sinks should be idempotent when duplicates matter.
- Connectors should make `ack()`, `retry()`, and `fail()` behavior durable according to their backend's normal delivery model.

For failure handling:

- handler, predicate, hook, sink, and timeout failures enter the same retry/dead-letter path unless explicitly suppressed by existing hook behavior
- retry policy controls whether the delivery is retried or failed
- dead-letter publish failure retries the original delivery
- delivery `fail()` failure falls back to retrying the original delivery
- cancellation retries the delivery

### Stop, Drain, Pause, And Cancellation

The runtime should document the current stop controls as operational contracts:

- `drain` stops new fetches and waits for inflight deliveries to finish
- `pause_task` stops new fetches for one task and waits for that task's inflight deliveries
- `resume_task` lets a paused task fetch again
- shutdown waits for inflight work up to `shutdown_timeout_s`
- sources that cannot safely cancel a fetch should set `fetch_is_cancel_safe = False` and implement `release_unstarted()`

These semantics already exist in code and tests; the work is to make them visible and keep them covered.

### YAML And Resource Contracts

Strict YAML mode should remain the contract surface for deployment configuration:

- unknown fields fail in strict mode
- resource handlers own field validation for plugin resource types
- `app.strict_env` catches missing environment variables without defaults
- YAML remains wiring, not business logic

Plugin resource registration should remain entry-point based through `onestep.resources`.

## Plugin Compatibility Gates

Core changes should be validated against plugin consumers in two layers.

First, keep the current per-plugin CI jobs. These jobs mirror real package ownership and avoid pytest module-name collisions.

Second, add or document a local compatibility command that runs every plugin suite in isolated pytest invocations. This preserves the current passing behavior while giving core maintainers one command for confidence before release.

The implementation should not require renaming all test files unless that proves simpler. An isolated runner script is acceptable because it matches CI behavior and avoids churn.

## Release Governance

Core releases should use a checklist with these decisions:

- Does this change affect stable application APIs?
- Does this change affect stable plugin APIs?
- Does this change alter delivery, retry, dead-letter, drain, pause, or cancellation semantics?
- Does this change alter reporter payloads, topology fields, task lifecycle events, or WebSocket behavior?
- Do any plugins need a lower-bound dependency update such as `onestep>=<new-version>`?
- Does `CHANGELOG.md` explain user-visible behavior and compatibility impact?
- Does the control plane need coordination?

The checklist should match the existing project rule: coordinate with `onestep-control-plane` only when protocol, reporter payloads, lifecycle event semantics, runtime identity, or remote-control behavior changes.

## Testing Strategy

The first implementation should prefer tests that lock down documented behavior rather than broad coverage for its own sake.

Required checks:

- existing non-integration suite: `uv run pytest -q -m "not integration"`
- each plugin suite in isolated invocations
- focused contract tests for any newly documented behavior that is not already tested
- packaging tests that confirm core imports do not require optional connector dependencies

Candidate contract gaps to check before adding tests:

- `ack()` failure after successful sink send follows the intended retry/failure path
- multi-sink partial success is documented and tested as non-transactional
- `fetch_is_cancel_safe = False` behavior is covered by core or plugin contract tests
- public API exports are covered by a lightweight import stability test

## Implementation Shape

The implementation should be small and staged:

1. Add documentation for stable API tiers and runtime semantics.
2. Add a local plugin compatibility runner or documented command.
3. Add only the missing focused contract tests discovered during implementation.
4. Add release checklist documentation.

Runtime code should change only if a contract test reveals behavior that is currently inconsistent, unsafe, or undocumented by design.

## Success Criteria

This work is done when:

- users can read one document and know which APIs are stable
- plugin authors can read one document and know what core contracts they can rely on
- at-least-once delivery and duplicate-output risk are stated plainly
- local verification can run core tests plus all plugin tests without pytest collection conflicts
- the release checklist makes core/plugin/control-plane coordination explicit
- no broad runtime refactor was introduced

## Open Decisions

The implementation plan should make these decisions before editing code:

- whether API tier documentation belongs in README, `docs/`, or both
- whether to solve plugin all-tests verification with a runner script or test renames
- whether low-level control-plane WebSocket classes should remain a stable public API or be documented as advanced/operational API
- whether ack-failure behavior needs a new contract test after inspecting current backend implementations


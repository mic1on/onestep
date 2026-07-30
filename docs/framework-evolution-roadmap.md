# onestep Framework Evolution Roadmap

## Product Position

onestep evolves as a code-first data task runtime. Python owns business
transformation; YAML wires resources, policies, and `handler.ref` values. The
framework competes on reliable delivery, reproducible failures, and operational
recovery rather than on a transformation DSL or connector count.

## Decision Rules

Every roadmap item must improve at least one of these outcomes:

1. reduce the probability of silently skipped or duplicated data;
2. reduce the time required to reproduce a production delivery locally;
3. reduce the time required to identify and recover a failed task;
4. reduce the work required to ship a connector that passes the same contracts
   as official connectors.

Items that do not improve one of these outcomes remain outside the committed
roadmap.

## Ordered Roadmap

| Order | Milestone | Deliverable | Exit gate |
| --- | --- | --- | --- |
| P0 | Connector correctness patches | Remove currently known ambiguous retry, state persistence, and direct API validation gaps | Focused regressions and every affected plugin suite pass |
| P1 | Connector conformance kit | Capability-based source, checkpoint, sink acknowledgement, failure classification, and redaction contracts | Every official plugin declares capabilities and passes applicable contracts |
| P2 | Local handler loop | One-delivery local execution, envelope capture/replay, resource connectivity checks, and handler scaffold tests | A captured failed delivery can be reproduced without a running worker or control plane |
| P3 | Delivery observability | Stable delivery correlation, lag/cursor/retry metrics, and audited recovery operations | An operator can identify a failed delivery and its attempts from the control plane |
| P4 | Plugin authoring platform | Plugin scaffold, catalog validation, compatibility matrix, support tiers, and CI template | A new connector can reach community support without copying an official plugin |
| P5 | Demand-led connector expansion | S3/MinIO first; later connectors require user evidence | The requested workload cannot be served cleanly by existing connectors |

## P0: Connector Correctness Patches

The first increment is defined in
[`docs/superpowers/plans/2026-07-29-connector-correctness-patches.md`](superpowers/plans/2026-07-29-connector-correctness-patches.md).

Scope:

- Elasticsearch/OpenSearch must not internally replay an ambiguous request-level
  502/503/504 response when documents have no stable IDs.
- MongoDB polling and change-stream sources must reject invalid direct Python
  numeric options during construction.
- MongoDB polling projections must preserve every effective cursor field
  without rewriting its value.
- MongoDB must not persist a later acknowledged token when an earlier delivery
  subsequently invalidates that generation for retry.
- MongoDB cursor state must not advance in memory or discard its pending prefix
  when durable state persistence fails.

This milestone is runtime/plugin internal and does not change reporter payloads,
task lifecycle events, WebSocket behavior, runtime identity, or remote controls.

## P1: Connector Conformance Kit

The conformance kit is capability-based rather than one universal test class.
Different backend types make different promises.

Required capability profiles:

- `basic_source`: fetch, terminal callback exclusivity, close idempotence;
- `checkpoint_source`: out-of-order acknowledgement, retry gap, durable state
  failure, and restart replay;
- `claimed_source`: `release_unstarted()` under drain, pause, shutdown, and
  cancellation;
- `acknowledged_sink`: return only after backend acknowledgement;
- `chunked_sink`: partial chunk commit classification;
- `replay_safe_sink`: stable-key convergence under repeated send;
- `public_errors`: normalized error kind and credential redaction.

Each official plugin publishes a small harness that supplies backend-specific
fixtures to the applicable profiles. Unsupported capabilities are declared, not
silently skipped. CI renders a connector support matrix from those declarations.

The implementation must remain separate from P0 so correctness patches are not
blocked by test-kit API design.

## P2: Local Handler Loop

Committed CLI surface:

```text
onestep task run <target> --task <name> --input <json-file>
onestep task replay <target> --task <name> --envelope <json-file>
onestep check --connect <target>
```

Design constraints:

- use the same handler invocation, task config, hooks, and sink routing as the
  worker runtime;
- make sink sending opt-in for local replay, with dry-run as the default;
- define a versioned, JSON-safe envelope capture format;
- redact resource secrets from diagnostics and captured envelopes;
- keep YAML as wiring; do not add transform expressions;
- generate handler tests and fixtures, not reusable business handlers.

The first P2 design must decide whether `run_task_once()` can be generalized or
whether a separate diagnostic runner is required. It must not overload the
existing remote manual-run semantics.

## P3: Delivery Observability

Introduce a stable delivery correlation value that survives retry and dead-letter
replay while individual attempts remain distinguishable. Propagate it through
`Envelope`, `TaskEvent`, structured logs, reporter payloads, control-plane storage,
and task-event views.

Because this changes reporter/topology data consumed by the control plane, P3
requires coordinated runtime, reporter plugin, backend schema, and frontend work.
All payload changes must be additive, and older planes must tolerate unknown JSON
fields.

Operational outputs:

- delivery and attempt correlation;
- source lag or cursor staleness where the backend exposes it;
- sink acknowledgement latency;
- retry counts grouped by normalized connector error kind;
- dead-letter inspect, replay, and discard audit records;
- deployed package checksum and effective configuration identity;
- drain and rollback workflow before worker replacement.

## P4: Plugin Authoring Platform

Provide:

```text
onestep create-plugin <name> --capability source|sink|both
```

The generated project contains a resource catalog entry, strict YAML validation,
Python API tests, applicable conformance harnesses, live integration-test wiring,
credential redaction tests, packaging metadata, and a release checklist.

Support tiers:

- `official`: maintained in the onestep repository, full conformance and live CI;
- `community`: external owner, declared conformance results and compatibility;
- `experimental`: API and behavior may change, no production support promise.

## P5: Connector Expansion

S3/MinIO is the next default candidate because it enables object ingestion,
archive output, data-lake exchange, and dead-letter storage. NATS JetStream comes
after demonstrated demand. Cloud-specific and SaaS connectors require a concrete
user workflow before entering the roadmap.

## Explicit Non-Goals

- no YAML field-transformation language;
- no official library of domain business handlers;
- no Airflow/Temporal-style DAG orchestration;
- no file-tail/syslog log-agent positioning;
- no database DDL or schema-migration ownership;
- no exactly-once claim;
- no connector-count target;
- no new control-plane surface before the corresponding recovery or diagnostic
  runtime capability exists.

## Success Metrics

| Metric | Initial target |
| --- | --- |
| New task to first successful local delivery | under 10 minutes |
| Captured production failure reproducible locally | at least 90% of handler failures |
| Official connectors with declared conformance profiles | 100% |
| Public connector failures mapped to normalized kinds | at least 95% |
| Failed delivery identification and replay | under 5 minutes |
| New community plugin to passing contract suite | under one working day |

## Release Sequence

1. Ship P0 as patch releases for affected plugins.
2. Design and ship P1 without changing application-facing runtime semantics.
3. Design P2 after P1 establishes reusable failure fixtures.
4. Coordinate P3 across runtime and control plane in one compatibility window.
5. Build P4 from the conformance and diagnostic APIs proven in P1 and P2.
6. Start P5 only after the platform can enforce the same quality bar on new
   connectors.

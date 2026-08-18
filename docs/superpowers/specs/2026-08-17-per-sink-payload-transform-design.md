# Per-Sink Payload Transform for Declarative Fan-Out

**Status:** Proposed design for Issue #116

**Date:** 2026-08-17

## Problem

OneStep tasks can emit one handler result to multiple sinks and can select sinks
with conditional routes. That works when every selected sink accepts the same
payload, but it cannot express a common fan-out where each sink needs a different
transport-shaped body.

The current workaround is to call ctx.emit(body, sink=...) from the handler and
keep only one sink in YAML. That makes the actual topology invisible to onestep
check and diagnostics, couples business code to resource names, and makes
dispatch order and failure behavior depend on arbitrary handler code.

Issue #116 proposes a declarative per-sink transform while keeping the transform
as a Python callable. This document defines the smallest runtime contract that
makes that proposal implementable without a workflow DSL or cross-sink
transaction.

## Goals

- Declare every production sink in tasks[].emit, so topology and resource
  lifecycle include all downstreams.
- Allow each sink binding to use a different synchronous or asynchronous Python
  transform.
- Preserve existing one-result broadcast and conditional-routing behavior.
- Evaluate every selected transform before sending to any selected sink.
- Keep existing at-least-once retry and acknowledgement semantics explicit.
- Expose transform references in task descriptions and diagnostics.

## Non-goals

- No YAML expression language, field-mapping DSL, or transport-specific transform
  syntax.
- No transactional or exactly-once commit across sinks.
- No per-sink retry, timeout, concurrency, or independent checkpoint.
- No change to ctx.emit(); it remains an imperative escape hatch for dynamic or
  side-effecting application code.
- No automatic conversion to independently consuming adapter tasks. That remains
  the recommended architecture when channels need independent SLAs, lag, replay,
  or dead-letter handling.

## User-facing YAML

Existing forms stay valid and unchanged:

~~~yaml
emit: audit_sink
emit: [processed_sink, audit_sink]
~~~

They broadcast the same handler result. Conditional routes stay valid and their
selected sinks still receive the same handler result:

~~~yaml
emit:
  - audit_sink
  - when:
      ref: worker.routing:is_active
    then: active_sink
    otherwise: inactive_sink
~~~

The new form declares one binding per sink:

~~~yaml
tasks:
  - name: extract_entities
    source: entity_events
    emit:
      - sink: entity_callback
      - sink: downstream_meta
        transform:
          ref: worker.transforms:to_meta_row
          params:
            include_source: true
    handler:
      ref: worker.tasks:extract_entities
~~~

Binding rules:

- sink is required and names exactly one registered Sink resource.
- transform is optional. Without it, the binding uses the handler result unchanged.
- transform is a callable reference string or a {ref, params} mapping, using the
  same callable-reference contract as handlers, hooks, and predicates.
- A binding mapping contains only sink and transform.
- A binding mapping cannot contain when, then, or otherwise. Conditional routing
  and per-sink bindings are separate forms in the first version.
- A task may mix legacy string entries and binding entries. A legacy string entry
  is an unchanged-result broadcast entry. New configurations should use one
  binding per sink whenever any sink needs a transform.
- An empty emit list remains invalid in strict mode.

This configuration is invalid and must fail validation:

~~~yaml
emit:
  - sink: downstream_meta
    transform: worker.transforms:to_meta_row
    then: another_sink
~~~

## Python transform contract

A transform is a projection function, not a sender:

~~~python
def to_meta_row(ctx, payload, result, *, include_source: bool):
    row = {
        "id": result["document_id"],
        "content": result["content"],
        "address": payload["address"],
    }
    if include_source:
        row["source"] = payload["source"]
    return row
~~~

The callable receives (ctx, payload, result), followed by configured keyword
parameters. payload is the original source-delivery payload and result is the
successful handler result. A synchronous return value or awaitable is accepted.
The returned value becomes the body of an Envelope sent to that binding's sink.

Transforms must not call ctx.emit(), choose a target, or replace the target sink.
Side effects belong in handlers or dedicated adapter tasks. A transform should be
deterministic for the same inputs whenever possible.

## Runtime model

The executor distinguishes an output binding from an already selected sink:

~~~text
source delivery
  -> handler -> result
  -> select legacy routes / bindings
  -> run every selected transform, in YAML order
  -> send every prepared envelope, in YAML order
  -> ack source delivery
~~~

Preparation is all-or-nothing with respect to sink side effects. If any selected
transform raises, no selected sink is sent. The task enters the existing failure
path with a transform stage and follows retry or dead-letter policy. A transform
failure is neither a handler success nor a sink failure.

Once the first sink send begins, dispatch remains at-least-once and
non-transactional. If an earlier sink succeeds and a later sink fails, the source
delivery is not acknowledged. A retry can send the earlier sink again. OneStep
does not roll back remote writes. Production sinks need a stable business key or
other idempotency mechanism whenever duplicates matter.

Existing None-result behavior remains: configured emit outputs are skipped. An
explicit ctx.emit() remains independent and is not converted into a binding
output. A task should not combine binding fan-out with explicit ctx.emit() to the
same sink unless duplicate delivery is intended.

## Internal model

The current EmitRoute models an unchanged-result route. Add a binding-level model
without changing the public TaskSpec.sinks compatibility view. A binding stores:

- the resolved target Sink;
- an optional transform callable;
- an optional transform_ref for descriptions, diagnostics, and tests.

Legacy sink entries normalize into bindings with no transform. Conditional routes
remain a distinct route type in the first version; EmitRoute must not combine both
predicate selection and per-sink projection.

TaskSpec.sinks remains the flattened tuple of every statically referenced sink.
That preserves resource ownership, startup and shutdown deduplication,
connectivity checks, and existing callers. Add a binding-aware detail view rather
than replacing the flattened compatibility field.

## Configuration and diagnostics

Strict validation distinguishes exactly three mapping shapes:

1. {when, then, otherwise}: existing conditional route.
2. {sink, transform}: new per-sink binding.
3. Anything else: invalid.

The callable loader reuses the existing {ref, params} helper so callable checks,
parameter copying, and async behavior remain consistent.

app.describe() and CLI summaries expose every binding sink and optional
transform_ref while retaining the flattened emit sink list. Diagnostics and
connectivity inventory enumerate every binding sink exactly once. Runtime
checkpoints retain selected_sinks and may add transform references as metadata,
but must not serialize payloads for diagnosis.

## Error handling

- Unknown binding keys, missing sink, empty sink names, invalid resources, and
  invalid transform references fail during strict configuration validation.
- A transform exception is a task-execution failure using the current retry and
  dead-letter policy. Existing `timeout_s` continues to cover only the handler;
  this change does not create a transform timeout.
- A sink-send exception keeps existing sink-failure and at-least-once behavior.
- The runtime passes the current source payload and handler result objects. A
  transform must not mutate them; defensive copying remains the callable's
  responsibility, consistent with handlers and hooks.

## Compatibility

- Existing Python emit=Sink, emit=[Sink, ...], EmitRoute, and YAML string/list
  forms remain valid.
- Existing ctx.emit(body, sink=...) behavior remains valid.
- Resource ownership and flattened sink summaries remain valid.
- A task with only unchanged-result bindings behaves like the equivalent legacy
  sink list, including ordering and failure behavior.
- No Sink plugin API changes are needed: every sink still receives one Envelope.

## Test plan

### Configuration and compatibility

- Parse a binding without transform and send its handler result unchanged.
- Parse a binding with a string transform reference.
- Parse {ref, params} and verify copied keyword parameters are passed.
- Reject missing sink, missing ref, unknown binding keys, and a mapping that mixes
  binding and conditional-route keys.
- Preserve legacy string, list, conditional-route parsing, and flattened
  TaskSpec.sinks.

### Runtime behavior

- Send distinct bodies to two sinks from one handler result.
- Await an async transform.
- Run transforms in YAML order before the first sink send.
- If the second transform fails, assert neither sink received a body and the task
  follows retry or dead-letter policy.
- If the second sink fails after the first succeeds, assert no source ack and the
  documented possibility of replaying the first sink on retry.
- Preserve None-result behavior and independent explicit ctx.emit().
- Verify transform failures are reported as transform-stage failures, not handler
  or sink failures.

### Topology and diagnostics

- app.describe() includes both binding sinks and transform references.
- CLI check output includes the complete sink topology.
- Connectivity inventory opens and closes every binding sink and does not
  duplicate a shared resource.

## Rollout and deferred decisions

This is a backward-compatible additive YAML and runtime change. Ship it with
focused core tests and strict YAML validation before production use.

The first implementation intentionally excludes:

- per-sink retry or timeout policies;
- parallel sink dispatch;
- persisted per-sink completion state;
- conditional routes whose selected sinks each have different transforms;
- automatic idempotency-key injection.

When channels require independent availability or replay, use a canonical event
and separate adapter tasks rather than extending this same-delivery fan-out.

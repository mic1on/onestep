# YAML Conditional Sink Routing Design

## Summary

Add conditional sink routing to YAML task definitions while keeping YAML as a wiring layer. The runtime will continue to treat Python as the place for business logic: YAML names the predicate callable and the target sinks, while Python evaluates the condition.

The recommended YAML shape is:

```yaml
tasks:
  - name: route_users
    source: incoming_users
    handler:
      ref: worker.tasks.users:normalize_user
    emit:
      - audit_sink
      - when:
          ref: worker.routing:is_active_user
          params:
            status: active
        then: active_user_sink
        otherwise: inactive_user_sink
```

This means:

- always emit handler results to `audit_sink`
- evaluate `worker.routing:is_active_user(ctx, payload, result, status="active")`
- emit to `active_user_sink` when the predicate is truthy
- emit to `inactive_user_sink` when the predicate is falsy

## Problem

YAML tasks currently support fan-out only. A handler result is sent to every configured sink in `tasks[].emit`.

That is too limited for common routing cases:

- active records should go to one sink and inactive records to another
- valid records should go downstream and invalid records should go to quarantine
- optional notifications should only be sent for selected results

Users can work around this today by moving sink publishing into the handler and bypassing `emit`, but that loses the clean runtime model:

- the task definition no longer shows where output goes
- routing is hidden inside business code
- `onestep check` and topology reporting cannot describe the sink graph
- YAML passthrough tasks cannot route at all

## Goals

- Add a YAML syntax for conditional routing to sinks.
- Preserve existing `emit: sink_name` and `emit: [sink_a, sink_b]` behavior.
- Keep condition logic in Python callables, not YAML expressions.
- Support both if/else routing and condition-only routing.
- Reuse existing callable reference conventions: `ref` plus optional `params`.
- Keep runtime dispatch centralized so all sink implementations behave the same way.

## Non-Goals

- No YAML expression language.
- No embedded Python snippets in YAML.
- No transform, branching, or workflow DSL in YAML.
- No first-version multi-case switch syntax.
- No connector-specific conditional behavior.
- No control-plane UI changes in the first implementation unless the topology protocol is deliberately expanded.

## User-Facing YAML

Existing unconditional forms remain valid:

```yaml
emit: processed
```

```yaml
emit: [processed, audit]
```

Conditional routing is expressed as an item inside `emit`:

```yaml
emit:
  - when:
      ref: worker.routing:is_active_user
    then: active_user_sink
    otherwise: inactive_user_sink
```

`otherwise` is optional:

```yaml
emit:
  - when:
      ref: worker.routing:should_notify
    then: notify_sink
```

This means "send to `notify_sink` only when the predicate is truthy; otherwise skip this emit entry."

`when` also supports the same shorthand accepted by hooks and handlers:

```yaml
emit:
  - when: worker.routing:should_notify
    then: notify_sink
```

`then` and `otherwise` accept either a single sink name or a non-empty list of sink names:

```yaml
emit:
  - when: worker.routing:is_active_user
    then: [active_user_sink, audit_active_sink]
    otherwise: [inactive_user_sink, audit_inactive_sink]
```

## Predicate Contract

A predicate is a Python callable referenced from YAML.

Preferred signature:

```python
def is_active_user(ctx, payload, result, *, status: str = "active") -> bool:
    return result.get("status") == status
```

Supported signatures should follow the existing callback convention used by hooks:

- `func(ctx, payload, result)`
- `func(ctx, payload)`
- `func(ctx)`
- `func()`

Async predicates are allowed:

```python
async def should_notify(ctx, payload, result) -> bool:
    return await ctx.resources["rules"].allows(result["tenant_id"])
```

Predicate return values use normal Python truthiness. A truthy value selects `then`; a falsy value selects `otherwise` when present, or skips the route when `otherwise` is absent.

## Runtime Semantics

The task flow remains:

1. Fetch delivery.
2. Run `before` hooks.
3. Invoke handler.
4. Run `after_success` hooks.
5. If the handler result is not `None`, resolve emit targets.
6. Send the result envelope to selected sinks.
7. Ack the delivery.
8. Emit task success event.

Conditional routing only affects step 5.

If a task has multiple `emit` entries, each entry is evaluated in order:

```yaml
emit:
  - audit_sink
  - when: worker.routing:is_active_user
    then: active_user_sink
  - when: worker.routing:should_notify
    then: notify_sink
```

This can send to multiple sinks for one result. Conditional routes are not exclusive unless the YAML author models them that way with `then` and `otherwise`.

When all conditional entries skip, the task still succeeds and the delivery is acked. "No sink selected" is a valid routing outcome, not a task failure.

## Failure Semantics

Predicate failures are task failures.

If a predicate raises, returns an awaitable that raises, or cannot be invoked, the runtime should treat that as the same class of failure as a handler or hook failure:

- no downstream sink sends from that failed route continue after the error
- the delivery follows the task retry policy
- dead-letter behavior stays unchanged
- failure events and logs use the existing task failure path

As with existing multi-sink fan-out, sink sends that already completed before a later predicate or sink failure are not rolled back. Retried deliveries may send to those earlier sinks again, so production sinks should remain idempotent when tasks use multiple emit targets.

Sink send failures also keep existing semantics:

- selected sinks are sent in configured order
- retryable connector errors use the shared sink retry path
- if a selected sink ultimately fails, the task fails and follows retry/dead-letter policy

The first version should not add per-route error handling such as `on_predicate_error` or fallback sinks for exceptions.

## Internal Model

Introduce an internal emit target model instead of changing individual sink classes.

Conceptual shape:

```python
@dataclass(frozen=True)
class EmitRoute:
    then_sinks: tuple[Sink, ...]
    predicate: Callable[..., Any] | None = None
    otherwise_sinks: tuple[Sink, ...] = ()
    predicate_ref: str | None = None
```

Unconditional emits normalize to:

```python
EmitRoute(then_sinks=(processed_sink,))
```

Conditional emits normalize to:

```python
EmitRoute(
    predicate=is_active_user,
    predicate_ref="worker.routing:is_active_user",
    then_sinks=(active_user_sink,),
    otherwise_sinks=(inactive_user_sink,),
)
```

`TaskSpec` should keep an ordered tuple of emit routes. For compatibility, `task.sinks` can continue to expose the flattened sink list or be backed by helper methods, depending on the smallest safe implementation.

The runtime send path should resolve selected sinks from the emit routes and continue using `TaskRunner._send_to_sink(...)` so sink-success logging and retry behavior stay centralized.

## YAML Parsing And Validation

Strict mode should accept `tasks[].emit` as:

- a string sink name
- a non-empty list of string sink names
- a non-empty list containing string sink names and conditional route mappings

Conditional route mapping fields:

- `when`: required; string ref or mapping with `ref` and optional `params`
- `then`: required; string sink name or non-empty list of sink names
- `otherwise`: optional; string sink name or non-empty list of sink names

Unknown fields under a conditional route should be rejected in strict mode.

Examples of invalid YAML:

```yaml
emit:
  - when: worker.routing:is_active_user
    otherwise: inactive_user_sink
```

`then` is required.

```yaml
emit:
  - when:
      expression: result.status == "active"
    then: active_user_sink
```

YAML expressions are not supported.

```yaml
emit:
  - when: worker.routing:is_active_user
    then: []
```

Empty sink lists are not meaningful and should be rejected.

## Passthrough Tasks

Passthrough tasks remain supported.

```yaml
tasks:
  - name: route_events
    source: incoming_events
    emit:
      - when: worker.routing:is_priority_event
        then: priority_sink
        otherwise: normal_sink
```

When a task has no `handler`, the existing passthrough handler returns the source payload. Predicates receive:

- `payload`: the original source payload
- `result`: the passthrough result, which is the same payload value

Strict mode should continue to require either `handler` or a non-empty `emit`. A conditional-only `emit` counts as configured emit.

## `ctx.emit()`

The first version should keep `ctx.emit(body, sink=...)` explicit and unconditional.

Reasons:

- YAML conditional routing applies to task-level automatic emit after handler success.
- `ctx.emit()` is already an imperative escape hatch inside Python code.
- A handler that needs custom runtime branching can call `ctx.emit()` directly with its own Python `if`.

If the implementation introduces shared internal dispatch helpers, `ctx.emit()` may use the same low-level send path for consistency, but it does not need to accept YAML-style routes in this design.

## Control Plane

The first implementation should avoid breaking the current control-plane protocol.

The control plane currently expects task topology `emit` to be a list of connector descriptors. Conditional route metadata does not fit that shape without a protocol change.

Recommended first-version behavior:

- report the flattened set of possible sink descriptors in `emit`
- do not include predicate refs or route structure in control-plane sync payloads
- keep topology hash stable for the flattened topology behavior

This preserves compatibility with existing control-plane ingestion and UI.

A later protocol version can add route metadata, for example:

```json
{
  "emit_routes": [
    {
      "when_ref": "worker.routing:is_active_user",
      "then": [{"kind": "mysql_table_sink", "name": "active"}],
      "otherwise": [{"kind": "mysql_table_sink", "name": "inactive"}]
    }
  ]
}
```

That should be designed with the control-plane repository because its Pydantic models, database storage, query API, and frontend rendering all currently assume connector descriptors.

## CLI And Description Output

`onestep check` should remain readable.

For JSON output, each task can continue exposing the existing flattened `emit` list for compatibility. If a richer route description is added, it should be additive, for example:

```json
{
  "emit": [
    {"name": "active_user_sink", "type": "MemoryQueue"},
    {"name": "inactive_user_sink", "type": "MemoryQueue"}
  ],
  "emit_routes": [
    {
      "when_ref": "worker.routing:is_active_user",
      "then": [{"name": "active_user_sink", "type": "MemoryQueue"}],
      "otherwise": [{"name": "inactive_user_sink", "type": "MemoryQueue"}]
    }
  ]
}
```

For human-readable output, keep the first version simple:

```text
- route_users source=incoming<MemoryQueue> emit=active_user_sink<MemoryQueue>,inactive_user_sink<MemoryQueue> ...
```

Route-aware formatting can be added later if needed.

## Backward Compatibility

This design is backward compatible.

- Existing YAML files remain valid.
- Existing Python apps remain valid.
- Existing connector implementations do not need changes.
- Existing sink retry and success logging behavior is preserved.
- Existing control-plane ingestion stays compatible if topology reporting remains flattened.

The only new behavior is enabled when a task uses a conditional route mapping in `emit`.

## Documentation Changes

Update:

- `docs/yaml-task-definition.md`
- `README.md` YAML section if a short example is useful
- bundled skill reference `skills/onestep/references/yaml-task-definition.md`

Documentation should emphasize:

- YAML declares the route; Python owns the condition
- `otherwise` is optional
- `then` and `otherwise` can point to one or more sinks
- conditional routes are evaluated in order and can coexist with unconditional sinks
- skipped routes do not make the task fail

## Testing Strategy

Add focused tests for five areas.

### YAML Loading

Cover:

- conditional route with `then` and `otherwise`
- conditional route without `otherwise`
- shorthand `when: module:function`
- mapping `when: {ref, params}`
- `then` and `otherwise` as single sink names and lists

### Strict Validation

Cover:

- missing `then`
- unknown conditional route field
- unsupported `when` shape
- empty `then` list
- non-sink resource referenced from `then` or `otherwise`

### Runtime Routing

Use `MemoryQueue` sinks.

Cover:

- truthy predicate sends to `then`
- falsy predicate sends to `otherwise`
- omitted `otherwise` skips without failure
- unconditional and conditional emit entries can both send
- async predicate is awaited
- predicate params are passed

### Failure Behavior

Cover:

- predicate exception follows task retry policy
- predicate exception can dead-letter after retries are exhausted
- sink failure after a selected route uses existing sink failure behavior

### Compatibility

Cover:

- old `emit: sink` and `emit: [sink_a, sink_b]` still load and run
- `onestep check --json` still includes the existing flattened `emit` shape
- reporter sync payload remains accepted by current control-plane schema if reporter tests cover topology

## Recommended Implementation Boundaries

Expected core files:

- `src/onestep/task.py`
  - add the internal emit route model and normalization
- `src/onestep/config.py`
  - parse and validate conditional route YAML
  - resolve predicate refs with existing callable binding helpers
  - resolve `then` and `otherwise` resources as sinks
- `src/onestep/runtime/runner.py`
  - evaluate routes after handler success
  - send only selected sinks through `_send_to_sink`
- `src/onestep/app.py`
  - include route sinks in resource startup/shutdown collection
  - preserve or augment `describe()`
- `src/onestep/reporter.py`
  - report flattened possible sinks unless the control-plane protocol is expanded
- `docs/yaml-task-definition.md`
  - document the feature
- `skills/onestep/references/yaml-task-definition.md`
  - update the bundled agent guidance
- tests in `tests/test_cli.py`, `tests/test_memory_queue.py`, or a focused runtime/config test module

Avoid editing connector plugins for the first version. The feature belongs in the shared runtime dispatch layer.

## Risks And Tradeoffs

### Hidden Business Logic

Predicates still contain business logic. That is intentional: YAML should name the predicate, not encode it. The route remains visible in YAML while the condition remains testable Python.

### Flattened Control-Plane Topology

Flattening possible sinks means the control plane can show that a task may emit to both `active` and `inactive`, but it cannot show the condition that selects between them. That is acceptable for a compatible first version. Route-aware topology should be a separate control-plane design.

### Multiple Conditional Entries

Multiple entries can all match and send to multiple sinks. This is flexible but can surprise users expecting exclusive routing. Documentation should state that `then`/`otherwise` is exclusive within one route entry, but separate `emit` entries are evaluated independently.

### Predicate Cost

Predicates run in the task execution path after handler success and before ack. Slow predicates delay delivery ack just like slow handlers or hooks. This is acceptable because predicates are user code and should be kept focused.

## Recommendation

Implement the YAML syntax:

```yaml
emit:
  - when:
      ref: worker.routing:predicate
      params: {}
    then: sink_or_sink_list
    otherwise: optional_sink_or_sink_list
```

This gives One Step the requested `when(condition, A sink, B sink)` behavior while preserving the existing YAML boundary: runtime wiring in YAML, business logic in Python.

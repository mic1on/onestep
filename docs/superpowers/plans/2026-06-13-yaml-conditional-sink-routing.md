# YAML Conditional Sink Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add YAML `emit` routes that choose `then` sinks or optional `otherwise` sinks by evaluating a Python predicate callable.

**Architecture:** Introduce an internal `EmitRoute` model in `onestep.task`, normalize all legacy emits into routes, and keep `TaskSpec.sinks` as the flattened compatibility view. YAML loading will parse route mappings with `when` / `then` / `otherwise`; the runner will evaluate selected routes after handler success and send through the existing `_send_to_sink` path.

**Tech Stack:** Python 3.11+, onestep core runtime, PyYAML-compatible config loading, pytest, existing `MemoryQueue` test sink, existing control-plane reporter JSON shape.

---

## Source Spec

Implement the accepted design in:

- `docs/superpowers/specs/2026-06-13-yaml-conditional-sink-routing-design.md`

The user-facing YAML syntax is:

```yaml
emit:
  - when:
      ref: worker.routing:predicate
      params: {}
    then: sink_or_sink_list
    otherwise: optional_sink_or_sink_list
```

## File Structure

- Modify `src/onestep/task.py`
  - Add `TaskPredicate` and `EmitRoute`.
  - Normalize legacy sink inputs into `EmitRoute` objects.
  - Keep `TaskSpec.sinks` as flattened possible sinks for compatibility.

- Modify `src/onestep/config.py`
  - Add strict route-field validation.
  - Parse `tasks[].emit` strings, lists of strings, and route mappings.
  - Resolve predicate refs with existing `_resolve_callable_ref`.
  - Resolve `then` / `otherwise` as sink names or non-empty sink-name lists.

- Modify `src/onestep/runtime/runner.py`
  - Evaluate routes after handler success and after `after_success` hooks.
  - Invoke predicates with the existing callback truncation behavior.
  - Send selected sinks through `_send_to_sink`.

- Modify `src/onestep/app.py`
  - Include all flattened route sinks in startup/shutdown resource collection.
  - Keep `describe()["tasks"][*]["emit"]` flattened.
  - Do not add route metadata to `describe()` in this implementation.

- Modify `src/onestep/reporter.py`
  - Keep control-plane topology `emit` flattened.
  - Do not send route metadata in this implementation.

- Modify `docs/yaml-task-definition.md`, `README.md`, and `skills/onestep/references/yaml-task-definition.md`
  - Document the new YAML route syntax, predicate contract, and failure semantics.

- Tests:
  - `tests/test_cli.py` for YAML loading, strict validation, and `onestep check` compatibility.
  - `tests/contract/test_runtime_contract.py` for runtime routing and failure semantics.
  - `tests/test_control_plane_reporter.py` for flattened reporter topology.

---

### Task 1: Add The Internal EmitRoute Model

**Files:**
- Modify: `src/onestep/task.py`
- Test: `tests/contract/test_runtime_contract.py`

- [ ] **Step 1: Write the failing model compatibility tests**

Add these imports in `tests/contract/test_runtime_contract.py`:

```python
from onestep.task import EmitRoute
```

Add these tests near the existing emit contract tests:

```python
def test_task_spec_normalizes_unconditional_sink_to_emit_route() -> None:
    source = MemoryQueue("incoming")
    sink = MemoryQueue("processed")
    app = OneStepApp("emit-route-model")

    @app.task(source=source, emit=sink)
    async def consume(ctx, item):
        return item

    task = app.tasks[0]
    assert task.sinks == (sink,)
    assert task.emit_routes == (EmitRoute(then_sinks=(sink,)),)


def test_task_spec_flattens_conditional_route_sinks_for_compatibility() -> None:
    source = MemoryQueue("incoming")
    active = MemoryQueue("active")
    inactive = MemoryQueue("inactive")
    app = OneStepApp("emit-route-flatten")

    def is_active(ctx, payload, result):
        return True

    route = EmitRoute(predicate=is_active, then_sinks=(active,), otherwise_sinks=(inactive,))

    @app.task(source=source, emit=[route])
    async def consume(ctx, item):
        return item

    task = app.tasks[0]
    assert task.emit_routes == (route,)
    assert task.sinks == (active, inactive)
```

- [ ] **Step 2: Run the focused tests and verify failure**

Run:

```bash
pytest tests/contract/test_runtime_contract.py::test_task_spec_normalizes_unconditional_sink_to_emit_route tests/contract/test_runtime_contract.py::test_task_spec_flattens_conditional_route_sinks_for_compatibility -q
```

Expected: FAIL because `EmitRoute` and `task.emit_routes` do not exist.

- [ ] **Step 3: Implement the minimal route model**

In `src/onestep/task.py`, update imports and add the model:

```python
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
```

Replace the task callable aliases with:

```python
TaskHandler = Callable[["TaskContext", Any], Any]
TaskHook = Callable[..., Any]
TaskPredicate = Callable[..., Any]
EmitTarget = Sink | "EmitRoute"
```

Add this dataclass after `TaskHooks`:

```python
@dataclass(frozen=True)
class EmitRoute:
    then_sinks: tuple[Sink, ...]
    predicate: TaskPredicate | None = None
    otherwise_sinks: tuple[Sink, ...] = ()
    predicate_ref: str | None = None

    @property
    def sinks(self) -> tuple[Sink, ...]:
        return (*self.then_sinks, *self.otherwise_sinks)
```

Update `TaskSpec` to store routes:

```python
@dataclass
class TaskSpec:
    name: str
    description: str | None
    handler: TaskHandler
    handler_ref: str | None
    source: Source | None
    sinks: tuple[Sink, ...]
    emit_routes: tuple[EmitRoute, ...]
    dead_letter_sinks: tuple[Sink, ...]
    config: dict[str, Any]
    metadata: dict[str, Any]
    hooks: TaskHooks
    concurrency: int
    retry: RetryPolicy
    timeout_s: float | None
```

Update `TaskSpec.build`:

```python
    def build(
        cls,
        *,
        name: str,
        description: str | None,
        handler: TaskHandler,
        handler_ref: str | None,
        source: Source | None,
        sinks: EmitTarget | Sequence[EmitTarget] | None,
        dead_letter: Sink | Sequence[Sink] | None,
        config: Mapping[str, Any] | None,
        metadata: Mapping[str, Any] | None,
        hooks: TaskHooks | None,
        concurrency: int,
        retry: RetryPolicy | None,
        timeout_s: float | None,
    ) -> "TaskSpec":
        if concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if timeout_s is not None and timeout_s <= 0:
            raise ValueError("timeout_s must be > 0")
        resolved_emit_routes = _normalize_emit_routes(sinks)
        resolved_dead_letter_sinks = _normalize_sinks(dead_letter)
        return cls(
            name=name,
            description=_normalize_description(description) or inspect.getdoc(handler),
            handler=handler,
            handler_ref=handler_ref,
            source=source,
            sinks=_flatten_emit_route_sinks(resolved_emit_routes),
            emit_routes=resolved_emit_routes,
            dead_letter_sinks=resolved_dead_letter_sinks,
            config=copy.deepcopy(dict(config or {})),
            metadata=copy.deepcopy(dict(metadata or {})),
            hooks=hooks or TaskHooks(),
            concurrency=concurrency,
            retry=retry or NoRetry(),
            timeout_s=timeout_s,
        )
```

Add helpers before `_normalize_sinks`:

```python
def _normalize_emit_routes(targets: EmitTarget | Sequence[EmitTarget] | None) -> tuple[EmitRoute, ...]:
    if targets is None:
        return ()
    if isinstance(targets, Sequence) and not isinstance(targets, (str, bytes)):
        entries = tuple(targets)
    else:
        entries = (targets,)

    routes: list[EmitRoute] = []
    for entry in entries:
        if isinstance(entry, EmitRoute):
            routes.append(entry)
        elif isinstance(entry, Sink):
            routes.append(EmitRoute(then_sinks=(entry,)))
        else:
            raise TypeError("emit entries must be Sink or EmitRoute instances")
    return tuple(routes)


def _flatten_emit_route_sinks(routes: Iterable[EmitRoute]) -> tuple[Sink, ...]:
    sinks: list[Sink] = []
    for route in routes:
        sinks.extend(route.sinks)
    return tuple(sinks)
```

- [ ] **Step 4: Run the focused tests and verify pass**

Run:

```bash
pytest tests/contract/test_runtime_contract.py::test_task_spec_normalizes_unconditional_sink_to_emit_route tests/contract/test_runtime_contract.py::test_task_spec_flattens_conditional_route_sinks_for_compatibility -q
```

Expected: PASS.

- [ ] **Step 5: Run existing emit contract tests**

Run:

```bash
pytest tests/contract/test_runtime_contract.py::test_ctx_emit_and_return_follow_separate_contracts tests/test_memory_queue.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/onestep/task.py tests/contract/test_runtime_contract.py
git commit -m "feat: add emit route model"
```

---

### Task 2: Parse YAML Conditional Emit Routes

**Files:**
- Modify: `src/onestep/config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing YAML load tests**

In `tests/test_cli.py`, add this test near `test_cli_check_loads_yaml_target`:

```python
def test_yaml_conditional_emit_routes_load_and_flatten_sinks(tmp_path) -> None:
    config_path = tmp_path / "conditional.yaml"
    config_path.write_text(
        json.dumps(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "conditional-yaml"},
                "resources": {
                    "incoming": {"type": "memory"},
                    "audit": {"type": "memory"},
                    "active": {"type": "memory"},
                    "inactive": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "route_users",
                        "source": "incoming",
                        "handler": "testsupport_yaml_routes:normalize",
                        "emit": [
                            "audit",
                            {
                                "when": {
                                    "ref": "testsupport_yaml_routes:is_active",
                                    "params": {"status": "active"},
                                },
                                "then": "active",
                                "otherwise": "inactive",
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    async def normalize(ctx, item):
        return item

    def is_active(ctx, payload, result, *, status: str):
        return result.get("status") == status

    with registered_yaml_module(), registered_module(
        "testsupport_yaml_routes",
        normalize=normalize,
        is_active=is_active,
    ):
        app = load_app_config(json.loads(config_path.read_text(encoding="utf-8")), strict=True)

    task = app.tasks[0]
    assert [sink.name for sink in task.sinks] == ["audit", "active", "inactive"]
    assert len(task.emit_routes) == 2
    assert task.emit_routes[0].predicate is None
    assert [sink.name for sink in task.emit_routes[0].then_sinks] == ["audit"]
    assert task.emit_routes[1].predicate_ref == "testsupport_yaml_routes:is_active"
    assert [sink.name for sink in task.emit_routes[1].then_sinks] == ["active"]
    assert [sink.name for sink in task.emit_routes[1].otherwise_sinks] == ["inactive"]
```

Add this list-form test:

```python
def test_yaml_conditional_emit_routes_accept_sink_lists(tmp_path) -> None:
    config = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "conditional-yaml-lists"},
        "resources": {
            "incoming": {"type": "memory"},
            "active": {"type": "memory"},
            "audit_active": {"type": "memory"},
            "inactive": {"type": "memory"},
            "audit_inactive": {"type": "memory"},
        },
        "tasks": [
            {
                "name": "route_users",
                "source": "incoming",
                "emit": [
                    {
                        "when": "testsupport_yaml_route_lists:is_active",
                        "then": ["active", "audit_active"],
                        "otherwise": ["inactive", "audit_inactive"],
                    }
                ],
            }
        ],
    }

    def is_active(ctx, payload, result):
        return True

    with registered_module("testsupport_yaml_route_lists", is_active=is_active):
        app = load_app_config(config, strict=True)

    route = app.tasks[0].emit_routes[0]
    assert [sink.name for sink in route.then_sinks] == ["active", "audit_active"]
    assert [sink.name for sink in route.otherwise_sinks] == ["inactive", "audit_inactive"]
    assert app.tasks[0].handler_ref is None
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
pytest tests/test_cli.py::test_yaml_conditional_emit_routes_load_and_flatten_sinks tests/test_cli.py::test_yaml_conditional_emit_routes_accept_sink_lists -q
```

Expected: FAIL because `_resolve_optional_sinks` only accepts string sink names.

- [ ] **Step 3: Add YAML route parsing**

In `src/onestep/config.py`, import `EmitRoute`:

```python
from .task import EmitRoute, TaskHooks
```

Add route fields near the other strict field constants:

```python
_STRICT_EMIT_ROUTE_FIELDS = frozenset({"when", "then", "otherwise"})
```

In `load_app_config`, replace:

```python
        emit = _resolve_optional_sinks(resources, task_config.get("emit"), field="emit", task_index=index)
```

with:

```python
        emit = _resolve_optional_emit_routes(resources, task_config.get("emit"), task_index=index)
```

Add these helpers after `_resolve_optional_sinks`:

```python
def _resolve_optional_emit_routes(
    resources: Mapping[str, Any],
    value: Any,
    *,
    task_index: int,
) -> tuple[EmitRoute, ...] | None:
    if value is None:
        return None
    field = f"tasks[{task_index}].emit"
    if isinstance(value, str):
        return (EmitRoute(then_sinks=_resolve_emit_sinks(resources, value, field=field)),)
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise TypeError(f"'{field}' must be a string or list of emit entries")
    routes: list[EmitRoute] = []
    for entry_index, entry in enumerate(value):
        entry_field = f"{field}[{entry_index}]"
        if isinstance(entry, str):
            routes.append(EmitRoute(then_sinks=_resolve_emit_sinks(resources, entry, field=entry_field)))
            continue
        if isinstance(entry, Mapping):
            routes.append(_resolve_emit_route(resources, entry, field=entry_field))
            continue
        raise TypeError(f"'{entry_field}' must be a sink name or emit route mapping")
    return tuple(routes)


def _resolve_emit_route(
    resources: Mapping[str, Any],
    raw_route: Mapping[str, Any],
    *,
    field: str,
) -> EmitRoute:
    _validate_unknown_fields(raw_route, _STRICT_EMIT_ROUTE_FIELDS, field=field)
    predicate, predicate_ref = _resolve_callable_ref(raw_route.get("when"), field=f"{field}.when")
    then_sinks = _resolve_emit_sinks(resources, raw_route.get("then"), field=f"{field}.then")
    otherwise_sinks = (
        _resolve_emit_sinks(resources, raw_route.get("otherwise"), field=f"{field}.otherwise")
        if "otherwise" in raw_route
        else ()
    )
    return EmitRoute(
        predicate=predicate,
        predicate_ref=predicate_ref,
        then_sinks=then_sinks,
        otherwise_sinks=otherwise_sinks,
    )


def _resolve_emit_sinks(resources: Mapping[str, Any], value: Any, *, field: str) -> tuple[Sink, ...]:
    names = _string_list(value, field=field)
    if not names:
        raise ValueError(f"'{field}' must not be empty")
    sinks: list[Sink] = []
    for name in names:
        resolved = _resolve_resource(resources, name)
        if not isinstance(resolved, Sink):
            raise TypeError(f"resource {name!r} cannot be used as a sink")
        sinks.append(resolved)
    return tuple(sinks)
```

- [ ] **Step 4: Run tests and verify pass**

Run:

```bash
pytest tests/test_cli.py::test_yaml_conditional_emit_routes_load_and_flatten_sinks tests/test_cli.py::test_yaml_conditional_emit_routes_accept_sink_lists -q
```

Expected: PASS.

- [ ] **Step 5: Run legacy YAML check tests**

Run:

```bash
pytest tests/test_cli.py::test_cli_check_loads_yaml_target tests/test_cli.py::test_cli_check_strict_loads_valid_yaml_target tests/test_http_sink.py::test_yaml_http_sink_and_passthrough_task_emit_payload -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/onestep/config.py tests/test_cli.py
git commit -m "feat: parse yaml conditional emit routes"
```

---

### Task 3: Add Strict Validation For Emit Routes

**Files:**
- Modify: `src/onestep/config.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Write failing strict-validation tests**

Add these tests to `tests/test_cli.py` near other strict YAML validation tests:

```python
def test_strict_yaml_rejects_conditional_emit_without_then() -> None:
    config = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "invalid-conditional"},
        "resources": {
            "incoming": {"type": "memory"},
            "inactive": {"type": "memory"},
        },
        "tasks": [
            {
                "name": "route",
                "source": "incoming",
                "emit": [
                    {
                        "when": "testsupport_invalid_routes:is_active",
                        "otherwise": "inactive",
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match=r"tasks\\[0\\]\\.emit\\[0\\]\\.then"):
        config_module.validate_app_config(config)


def test_strict_yaml_rejects_conditional_emit_unknown_field() -> None:
    config = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "invalid-conditional"},
        "resources": {
            "incoming": {"type": "memory"},
            "active": {"type": "memory"},
        },
        "tasks": [
            {
                "name": "route",
                "source": "incoming",
                "emit": [
                    {
                        "when": "testsupport_invalid_routes:is_active",
                        "then": "active",
                        "else": "active",
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match=r"unknown field.*tasks\\[0\\]\\.emit\\[0\\].*else"):
        config_module.validate_app_config(config)


def test_strict_yaml_rejects_conditional_emit_expression_shape() -> None:
    config = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "invalid-conditional"},
        "resources": {
            "incoming": {"type": "memory"},
            "active": {"type": "memory"},
        },
        "tasks": [
            {
                "name": "route",
                "source": "incoming",
                "emit": [
                    {
                        "when": {"expression": "result.status == 'active'"},
                        "then": "active",
                    }
                ],
            }
        ],
    }

    with pytest.raises(ValueError, match=r"unknown field.*tasks\\[0\\]\\.emit\\[0\\]\\.when.*expression"):
        config_module.validate_app_config(config)
```

- [ ] **Step 2: Run strict tests and verify failure**

Run:

```bash
pytest tests/test_cli.py::test_strict_yaml_rejects_conditional_emit_without_then tests/test_cli.py::test_strict_yaml_rejects_conditional_emit_unknown_field tests/test_cli.py::test_strict_yaml_rejects_conditional_emit_expression_shape -q
```

Expected: FAIL because strict validation does not inspect emit route mappings yet.

- [ ] **Step 3: Implement strict route validation**

In `src/onestep/config.py`, update `_validate_tasks` by adding emit validation before handler validation:

```python
        _validate_emit(raw_task.get("emit"), field=f"{field}.emit")
        if "handler" in raw_task:
            _validate_ref_entry(raw_task.get("handler"), field=f"{field}.handler")
        elif not _task_emit_configured(raw_task.get("emit")):
            raise ValueError(f"{field} must define either 'handler' or 'emit'")
```

Add these helpers before `_validate_retry`:

```python
def _validate_emit(raw_emit: Any, *, field: str) -> None:
    if raw_emit is None:
        return
    if isinstance(raw_emit, str):
        _string_value(raw_emit, field=field)
        return
    if not isinstance(raw_emit, Sequence) or isinstance(raw_emit, (str, bytes)):
        raise TypeError(f"'{field}' must be a string or list of emit entries")
    if not raw_emit:
        raise ValueError(f"'{field}' must not be empty")
    for index, entry in enumerate(raw_emit):
        entry_field = f"{field}[{index}]"
        if isinstance(entry, str):
            _string_value(entry, field=entry_field)
            continue
        if isinstance(entry, Mapping):
            _validate_emit_route(entry, field=entry_field)
            continue
        raise TypeError(f"'{entry_field}' must be a sink name or emit route mapping")


def _validate_emit_route(raw_route: Mapping[str, Any], *, field: str) -> None:
    _validate_unknown_fields(raw_route, _STRICT_EMIT_ROUTE_FIELDS, field=field)
    _validate_ref_entry(raw_route.get("when"), field=f"{field}.when")
    _validate_emit_sink_names(raw_route.get("then"), field=f"{field}.then")
    if "otherwise" in raw_route:
        _validate_emit_sink_names(raw_route.get("otherwise"), field=f"{field}.otherwise")


def _validate_emit_sink_names(raw_value: Any, *, field: str) -> None:
    names = _string_list(raw_value, field=field)
    if not names:
        raise ValueError(f"'{field}' must not be empty")
```

- [ ] **Step 4: Run strict tests and verify pass**

Run:

```bash
pytest tests/test_cli.py::test_strict_yaml_rejects_conditional_emit_without_then tests/test_cli.py::test_strict_yaml_rejects_conditional_emit_unknown_field tests/test_cli.py::test_strict_yaml_rejects_conditional_emit_expression_shape -q
```

Expected: PASS.

- [ ] **Step 5: Run strict YAML suite subset**

Run:

```bash
pytest tests/test_cli.py -k "strict or conditional_emit" -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/onestep/config.py tests/test_cli.py
git commit -m "feat: validate yaml conditional emit routes"
```

---

### Task 4: Evaluate Routes In The Runtime Runner

**Files:**
- Modify: `src/onestep/runtime/runner.py`
- Test: `tests/contract/test_runtime_contract.py`

- [ ] **Step 1: Write failing runtime routing tests**

Add these tests near the existing emit contract tests:

```python
def test_conditional_emit_route_sends_then_or_otherwise() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming")
        active = MemoryQueue("active")
        inactive = MemoryQueue("inactive")
        app = OneStepApp("conditional-route-runtime")

        def is_active(ctx, payload, result):
            return result["status"] == "active"

        @app.task(
            source=source,
            emit=[
                EmitRoute(
                    predicate=is_active,
                    then_sinks=(active,),
                    otherwise_sinks=(inactive,),
                )
            ],
        )
        async def route(ctx, item):
            if item["id"] == 1:
                return {"id": 1, "status": "active"}
            ctx.app.request_shutdown()
            return {"id": 2, "status": "inactive"}

        await source.publish({"id": 1})
        await source.publish({"id": 2})
        await app.serve()

        active_items = await active.fetch(10)
        inactive_items = await inactive.fetch(10)
        assert [delivery.payload for delivery in active_items] == [{"id": 1, "status": "active"}]
        assert [delivery.payload for delivery in inactive_items] == [{"id": 2, "status": "inactive"}]

    asyncio.run(scenario())


def test_conditional_emit_route_without_otherwise_skips_without_failure() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming")
        notify = MemoryQueue("notify")
        app = OneStepApp("conditional-route-skip")

        def should_notify(ctx, payload, result):
            return False

        @app.task(source=source, emit=[EmitRoute(predicate=should_notify, then_sinks=(notify,))])
        async def route(ctx, item):
            ctx.app.request_shutdown()
            return {"id": item["id"]}

        await source.publish({"id": 1})
        await app.serve()

        assert notify.size() == 0

    asyncio.run(scenario())
```

- [ ] **Step 2: Run runtime tests and verify failure**

Run:

```bash
pytest tests/contract/test_runtime_contract.py::test_conditional_emit_route_sends_then_or_otherwise tests/contract/test_runtime_contract.py::test_conditional_emit_route_without_otherwise_skips_without_failure -q
```

Expected: FAIL because the runner currently sends every flattened `task.sinks` entry unconditionally.

- [ ] **Step 3: Implement route selection**

In `src/onestep/runtime/runner.py`, replace this block in `_handle_delivery`:

```python
            if result is not None and self.task.sinks:
                envelope = Envelope(body=result)
                for sink in self.task.sinks:
                    await self._send_to_sink(sink, envelope)
```

with:

```python
            if result is not None and self.task.emit_routes:
                envelope = Envelope(body=result)
                for sink in await self._select_emit_sinks(ctx, delivery.payload, result):
                    await self._send_to_sink(sink, envelope)
```

Add this helper before `_invoke_handler`:

```python
    async def _select_emit_sinks(self, ctx: TaskContext, payload: Any, result: Any):
        selected = []
        for route in self.task.emit_routes:
            if route.predicate is None:
                selected.extend(route.then_sinks)
                continue
            predicate_result = invoke_callback(route.predicate, ctx, payload, result)
            if inspect.isawaitable(predicate_result):
                predicate_result = await predicate_result
            selected.extend(route.then_sinks if predicate_result else route.otherwise_sinks)
        return tuple(selected)
```

- [ ] **Step 4: Run runtime tests and verify pass**

Run:

```bash
pytest tests/contract/test_runtime_contract.py::test_conditional_emit_route_sends_then_or_otherwise tests/contract/test_runtime_contract.py::test_conditional_emit_route_without_otherwise_skips_without_failure -q
```

Expected: PASS.

- [ ] **Step 5: Run broader runtime contract subset**

Run:

```bash
pytest tests/contract/test_runtime_contract.py::test_ctx_emit_and_return_follow_separate_contracts tests/contract/test_runtime_contract.py::test_task_timeout_retries_once_then_fails tests/test_memory_queue.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/onestep/runtime/runner.py tests/contract/test_runtime_contract.py
git commit -m "feat: route handler results to conditional sinks"
```

---

### Task 5: Cover Async Predicates, Params, And Failure Semantics

**Files:**
- Modify: `tests/contract/test_runtime_contract.py`
- Modify: `tests/test_cli.py`
- Modify: `src/onestep/runtime/runner.py` only if tests reveal a bug

- [ ] **Step 1: Add async predicate and params runtime tests**

Add this test to `tests/contract/test_runtime_contract.py`:

```python
def test_conditional_emit_route_awaits_async_predicate() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming")
        selected = MemoryQueue("selected")
        app = OneStepApp("conditional-route-async")

        async def should_select(ctx, payload, result):
            await asyncio.sleep(0)
            return result["selected"]

        @app.task(source=source, emit=[EmitRoute(predicate=should_select, then_sinks=(selected,))])
        async def route(ctx, item):
            ctx.app.request_shutdown()
            return {"selected": True}

        await source.publish({"id": 1})
        await app.serve()

        batch = await selected.fetch(1)
        assert len(batch) == 1
        assert batch[0].payload == {"selected": True}

    asyncio.run(scenario())
```

Add this YAML params test to `tests/test_cli.py`:

```python
def test_yaml_conditional_emit_route_passes_predicate_params() -> None:
    config = {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "conditional-params"},
        "resources": {
            "incoming": {"type": "memory"},
            "matched": {"type": "memory"},
        },
        "tasks": [
            {
                "name": "route",
                "source": "incoming",
                "emit": [
                    {
                        "when": {
                            "ref": "testsupport_conditional_params:matches_status",
                            "params": {"status": "active"},
                        },
                        "then": "matched",
                    }
                ],
            }
        ],
    }
    seen = []

    def matches_status(ctx, payload, result, *, status: str):
        seen.append((payload["status"], result["status"], status))
        return result["status"] == status

    async def scenario() -> None:
        with registered_module("testsupport_conditional_params", matches_status=matches_status):
            app = load_app_config(config, strict=True)
            await app.startup()
            try:
                await app.run_task_once("route", payload={"status": "active"})
            finally:
                await app.shutdown()

            batch = await app.resources["matched"].fetch(1)
            assert len(batch) == 1
            assert batch[0].payload == {"status": "active"}

    asyncio.run(scenario())
    assert seen == [("active", "active", "active")]
```

- [ ] **Step 2: Add predicate failure retry test**

Add this test to `tests/contract/test_runtime_contract.py`:

```python
def test_conditional_emit_predicate_failure_uses_retry_policy() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        selected = MemoryQueue("selected")
        app = OneStepApp("conditional-route-failure")
        predicate_calls = 0

        def flaky_predicate(ctx, payload, result):
            nonlocal predicate_calls
            predicate_calls += 1
            if predicate_calls == 1:
                raise RuntimeError("predicate unavailable")
            ctx.app.request_shutdown()
            return True

        @app.task(
            source=source,
            emit=[EmitRoute(predicate=flaky_predicate, then_sinks=(selected,))],
            retry=MaxAttempts(2, delay_s=0),
        )
        async def route(ctx, item):
            return {"value": item["value"]}

        await source.publish({"value": 7})
        await app.serve()

        batch = await selected.fetch(1)
        assert predicate_calls == 2
        assert len(batch) == 1
        assert batch[0].payload == {"value": 7}

    asyncio.run(scenario())
```

- [ ] **Step 3: Run new tests**

Run:

```bash
pytest tests/contract/test_runtime_contract.py::test_conditional_emit_route_awaits_async_predicate tests/contract/test_runtime_contract.py::test_conditional_emit_predicate_failure_uses_retry_policy tests/test_cli.py::test_yaml_conditional_emit_route_passes_predicate_params -q
```

Expected: PASS.

- [ ] **Step 4: Run failure-path regression tests**

Run:

```bash
pytest tests/contract/test_runtime_contract.py -k "retry or dead_letter or conditional_emit" -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/contract/test_runtime_contract.py tests/test_cli.py src/onestep/runtime/runner.py
git commit -m "test: cover conditional emit route semantics"
```

---

### Task 6: Preserve Reporter And CLI Compatibility

**Files:**
- Modify: `src/onestep/app.py`
- Modify: `src/onestep/reporter.py`
- Test: `tests/test_control_plane_reporter.py`
- Test: `tests/test_cli.py`

- [ ] **Step 1: Add reporter flattened-topology test**

Add this test to `tests/test_control_plane_reporter.py` near `test_reporter_sync_payload_includes_task_topology`:

```python
def test_reporter_sync_payload_flattens_conditional_emit_routes() -> None:
    recorder = SenderRecorder()
    source = MemoryQueue("incoming")
    active = MemoryQueue("active")
    inactive = MemoryQueue("inactive")
    app = OneStepApp("conditional-topology")

    def is_active(ctx, payload, result):
        return True

    @app.task(
        source=source,
        emit=[EmitRoute(predicate=is_active, then_sinks=(active,), otherwise_sinks=(inactive,))],
    )
    async def route(ctx, payload):
        return payload

    reporter = ControlPlaneReporter(_make_config(service_name="conditional-topology"), sender=recorder)
    reporter.attach(app)

    async def scenario() -> None:
        await app.startup()
        await app.shutdown()

    asyncio.run(scenario())

    sync_payload = [payload for channel, payload in recorder.calls if channel == "sync"][0]
    emit = sync_payload["app"]["tasks"][0]["emit"]
    assert [entry["name"] for entry in emit] == ["active", "inactive"]
    assert all("when_ref" not in entry for entry in emit)
```

Add the import:

```python
from onestep.task import EmitRoute
```

- [ ] **Step 2: Add CLI JSON flattened output test**

Add this assertion inside `test_yaml_conditional_emit_routes_load_and_flatten_sinks` or add a separate test:

```python
def test_cli_check_json_flattens_conditional_emit_routes(capsys, tmp_path) -> None:
    config_path = tmp_path / "conditional-cli.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {"name": "conditional-cli"},
                "resources": {
                    "incoming": {"type": "memory"},
                    "active": {"type": "memory"},
                    "inactive": {"type": "memory"},
                },
                "tasks": [
                    {
                        "name": "route",
                        "source": "incoming",
                        "emit": [
                            {
                                "when": "testsupport_conditional_cli:is_active",
                                "then": "active",
                                "otherwise": "inactive",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    def is_active(ctx, payload, result):
        return True

    with registered_yaml_module(), registered_module("testsupport_conditional_cli", is_active=is_active):
        exit_code = main(["check", "--json", str(config_path)])

    captured = capsys.readouterr()
    summary = json.loads(captured.out)
    assert exit_code == 0
    assert [entry["name"] for entry in summary["tasks"][0]["emit"]] == ["active", "inactive"]
```

- [ ] **Step 3: Run compatibility tests**

Run:

```bash
pytest tests/test_control_plane_reporter.py::test_reporter_sync_payload_flattens_conditional_emit_routes tests/test_cli.py::test_cli_check_json_flattens_conditional_emit_routes -q
```

Expected: PASS if `TaskSpec.sinks` remains flattened. If this fails, inspect `src/onestep/app.py:startup`, `src/onestep/app.py:describe`, and `src/onestep/reporter.py:_build_app_topology_descriptor` and make them use `task.sinks`.

- [ ] **Step 4: Verify no control-plane protocol drift**

Run:

```bash
pytest tests/test_control_plane_reporter.py::test_reporter_sync_payload_includes_task_topology tests/test_runtime_descriptor.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/onestep/app.py src/onestep/reporter.py tests/test_control_plane_reporter.py tests/test_cli.py
git commit -m "test: preserve flattened emit topology"
```

---

### Task 7: Document YAML Conditional Sink Routing

**Files:**
- Modify: `docs/yaml-task-definition.md`
- Modify: `README.md`
- Modify: `skills/onestep/references/yaml-task-definition.md`

- [ ] **Step 1: Update `docs/yaml-task-definition.md`**

Add this section after the existing "Level 2: Add Sinks And Runtime Policy" example:

```markdown
### Conditional Sink Routing

YAML can route handler results to different sinks by referencing a Python predicate.
YAML declares the route; Python owns the condition logic.

```yaml
tasks:
  - name: route_users
    source: users_source
    handler:
      ref: worker.tasks.users:normalize_user
    emit:
      - audit_stream
      - when:
          ref: worker.routing:is_active_user
          params:
            status: active
        then: active_users_sink
        otherwise: inactive_users_sink
```

The predicate receives `(ctx, payload, result)` and may be async:

```python
def is_active_user(ctx, payload, result, *, status: str) -> bool:
    return result.get("status") == status
```

When the predicate is truthy, the runtime sends to `then`. When it is falsy,
the runtime sends to `otherwise` if provided. If `otherwise` is omitted, the
route is skipped and the task can still succeed.

`then` and `otherwise` can be a sink name or a non-empty list of sink names.
Separate `emit` entries are evaluated independently, so more than one route can
send for the same handler result.
```

- [ ] **Step 2: Update README YAML section**

Add this short paragraph after the existing YAML resources paragraph:

```markdown
YAML `emit` entries can also route to sinks with a Python predicate:

```yaml
emit:
  - when:
      ref: your_package.routing:is_active
    then: active_sink
    otherwise: inactive_sink
```

The predicate is regular Python, receives `(ctx, payload, result)`, and returns
a truthy value for `then` or a falsy value for `otherwise`.
```

- [ ] **Step 3: Update bundled skill reference**

In `skills/onestep/references/yaml-task-definition.md`, add this under "Sinks And Runtime Policy":

```markdown
Conditional sink routing stays in YAML wiring while the condition stays in Python:

```yaml
tasks:
  - name: route_users
    source: users_source
    handler:
      ref: worker.tasks.users:normalize_user
    emit:
      - when:
          ref: worker.routing:is_active_user
        then: active_users_sink
        otherwise: inactive_users_sink
```

Do not write expressions in YAML. Put the predicate in Python and reference it
with `when.ref`. `otherwise` is optional; omitting it means the route skips when
the predicate is falsy.
```

- [ ] **Step 4: Run docs-adjacent checks**

Run:

```bash
rg -n "conditional|when:|otherwise" docs/yaml-task-definition.md README.md skills/onestep/references/yaml-task-definition.md
python -m compileall src/onestep
```

Expected: `rg` prints the new documentation lines and `compileall` exits 0.

- [ ] **Step 5: Commit**

```bash
git add docs/yaml-task-definition.md README.md skills/onestep/references/yaml-task-definition.md
git commit -m "docs: document yaml conditional sink routing"
```

---

### Task 8: Full Verification And Release Notes

**Files:**
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update changelog**

Add this bullet under the current unreleased or latest feature section in `CHANGELOG.md`:

```markdown
- Adds YAML conditional sink routing with `when` / `then` / `otherwise` emit entries, while preserving legacy fan-out emit behavior.
```

- [ ] **Step 2: Run focused full verification**

Run:

```bash
pytest tests/test_cli.py tests/test_memory_queue.py tests/test_control_plane_reporter.py tests/contract/test_runtime_contract.py -q
```

Expected: PASS.

- [ ] **Step 3: Run package checks**

Run:

```bash
ruff check src tests
python -m compileall src/onestep
git diff --check
```

Expected:

- `ruff check src tests` exits 0.
- `python -m compileall src/onestep` exits 0.
- `git diff --check` prints no output and exits 0.

- [ ] **Step 4: Run a strict YAML smoke check**

Create `/tmp/onestep-conditional-route-smoke/worker.yaml` with:

```yaml
apiVersion: onestep/v1alpha1
kind: App

app:
  name: conditional-route-smoke

resources:
  incoming:
    type: memory
  active:
    type: memory
  inactive:
    type: memory

tasks:
  - name: route
    source: incoming
    emit:
      - when:
          ref: smoke_routes:is_active
        then: active
        otherwise: inactive
```

Create `/tmp/onestep-conditional-route-smoke/smoke_routes.py` with:

```python
def is_active(ctx, payload, result):
    return result.get("active", False)
```

Run:

```bash
PYTHONPATH=/tmp/onestep-conditional-route-smoke:src python -m onestep.cli check --strict /tmp/onestep-conditional-route-smoke/worker.yaml
```

Expected: exit 0 and output includes `App: conditional-route-smoke`.

- [ ] **Step 5: Commit changelog and any final fixes**

```bash
git add CHANGELOG.md
git commit -m "chore: note yaml conditional routing"
```

- [ ] **Step 6: Final status check**

Run:

```bash
git status --short
```

Expected: no output.

---

## Self-Review Checklist

- Spec coverage:
  - YAML syntax with `when` / `then` / `otherwise`: Tasks 2, 3, and 7.
  - Predicate `ref` / `params`: Tasks 2 and 5.
  - Async predicate: Task 5.
  - `otherwise` optional skip behavior: Task 4.
  - Legacy emit compatibility: Tasks 1, 2, and 6.
  - Runtime failure and retry semantics: Task 5.
  - Control-plane flattened compatibility: Task 6.
  - Documentation and bundled skill update: Task 7.

- Type consistency:
  - `EmitRoute` is defined in `src/onestep/task.py`.
  - `TaskSpec.emit_routes` is the ordered runtime route list.
  - `TaskSpec.sinks` remains the flattened compatibility sink list.
  - YAML parsing returns `tuple[EmitRoute, ...]` through `_resolve_optional_emit_routes`.
  - Runtime uses `invoke_callback(route.predicate, ctx, payload, result)`.

- Final success criteria:
  - Existing YAML files still load.
  - Conditional YAML routes send only selected sinks.
  - Skipped conditional routes do not fail tasks.
  - Predicate exceptions use normal task failure policy.
  - Control-plane reporter emits flattened `emit` descriptors only.
  - Focused pytest, ruff, compileall, and diff checks pass.

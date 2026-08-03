# YAML Logging Level And Sink Success Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a pure-YAML `app.logging.level` setting and emit uniform DEBUG-level sink-success logs from the runtime send path.

**Architecture:** Extend strict YAML validation so `app.logging.level` is a supported app-level field, then apply that level to the `onestep` logger namespace during YAML app loading. Emit sink-success logs once in `TaskRunner._send_to_sink(...)` so all sink types share the same observability path, while keeping connector implementations focused on transport behavior only.

**Tech Stack:** Python logging, YAML config loader, pytest, existing OneStep runtime and connector abstractions

---

### Task 1: Add YAML logging config and apply it at load time

**Files:**
- Modify: `src/onestep/config.py:33-51,257-348,843-871`
- Test: `tests/test_cli.py:430-550`

- [ ] **Step 1: Write the failing test**

Add a strict YAML config test that loads a config with:

```python
{
    "apiVersion": "onestep/v1alpha1",
    "kind": "App",
    "app": {
        "name": "yaml-logging",
        "logging": {"level": "debug"},
    },
    "tasks": [],
}
```

Assert that:

```python
app = load_app_config(config, strict=True)
assert logging.getLogger("onestep").level == logging.DEBUG
```

Add negative cases in the same file for:

```python
{"app": {"name": "bad", "logging": {"level": 123}}}
{"app": {"name": "bad", "logging": {"level": "verbose"}}}
{"app": {"name": "bad", "logging": {"unexpected": True}}}
```

Assert the loader raises:

```python
TypeError
ValueError
ValueError
```

with messages that mention `app.logging.level` or `unsupported fields for app.logging`.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_cli.py -k logging -v
```

Expected:

- the new assertions fail because `app.logging` is not yet accepted
- strict validation rejects `logging` as an unknown app field

- [ ] **Step 3: Write minimal implementation**

In `src/onestep/config.py`:

```python
_STRICT_APP_FIELDS = frozenset({"name", "shutdown_timeout_s", "config", "state", "logging"})
_STRICT_APP_LOGGING_FIELDS = frozenset({"level"})
```

Add a helper:

```python
def _apply_app_logging(app: OneStepApp, raw_logging: Any) -> None:
    if raw_logging is None:
        return
    if not isinstance(raw_logging, Mapping):
        raise TypeError("'app.logging' must be a mapping")
    _validate_unknown_fields(raw_logging, _STRICT_APP_LOGGING_FIELDS, field="app.logging")
    level = _require_string(raw_logging, "level")
    resolved = getattr(logging, level.strip().upper(), None)
    if not isinstance(resolved, int):
        raise ValueError(f"unsupported logging level {level!r}")
    logging.getLogger("onestep").setLevel(resolved)
```

Call it from `load_app_config(...)` after creating `OneStepApp` and before wiring tasks.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/test_cli.py -k logging -v
```

Expected:

- the new strict validation tests pass
- logger level is set when `app.logging.level` is present
- logger state is left unchanged when the field is omitted

- [ ] **Step 5: Commit**

```bash
git add src/onestep/config.py tests/test_cli.py
git commit -m "feat: add yaml logging level config"
```

### Task 2: Emit uniform sink-success debug logs

**Files:**
- Modify: `src/onestep/runtime/runner.py:200-248`
- Test: `tests/contract/test_runtime_contract.py:1-120,788-875`

- [ ] **Step 1: Write the failing test**

Add a runtime contract test using `MemoryQueue` and a `logging.Handler` that captures records from `logging.getLogger("onestep.logging-contract")`.

The test should:

```python
app = OneStepApp("logging-contract")
logger = logging.getLogger("onestep.logging-contract")
handler = ListHandler()
logger.handlers = [handler]
logger.setLevel(logging.DEBUG)
logger.propagate = False

source = MemoryQueue("incoming")
sink = MemoryQueue("processed")

@app.task(source=source, emit=sink)
async def consume(ctx, item):
    ctx.app.request_shutdown()
    return {"value": item["value"] + 1}
```

Publish one item, run `await app.serve()`, and assert one captured record has:

```python
record.levelno == logging.DEBUG
record.message == "sink send succeeded"
record.sink_name == "processed"
```

Add a second assertion that the same test produces no success record if the logger level is raised above DEBUG.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/contract/test_runtime_contract.py -k sink_success -v
```

Expected:

- no debug success record exists yet
- only the current task-level success event exists

- [ ] **Step 3: Write minimal implementation**

Change `TaskRunner._send_to_sink(...)` so successful sends log once before returning:

```python
self._logger.debug(
    "sink send succeeded",
    extra={
        "sink_name": getattr(sink, "name", sink.__class__.__name__),
        "sink_kind": sink.__class__.__name__,
        "connector_backend": getattr(sink, "backend", None),
        "delivery_attempts": envelope.attempts,
    },
)
```

Keep the existing retry warning path unchanged. Do not touch individual connector `send()` methods.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/contract/test_runtime_contract.py -k sink_success -v
```

Expected:

- successful sink sends now produce one DEBUG record
- failure and retry behavior stays unchanged

- [ ] **Step 5: Commit**

```bash
git add src/onestep/runtime/runner.py tests/contract/test_runtime_contract.py
git commit -m "feat: log sink sends at debug level"
```

### Task 3: Document the new YAML field and logging behavior

**Files:**
- Modify: `docs/yaml-task-definition.md:1-160`
- Modify: `README.md:450-520`

- [ ] **Step 1: Write the failing documentation check**

Add a small doc test or review target by editing the docs so the YAML examples include:

```yaml
app:
  name: hello-worker
  logging:
    level: DEBUG
```

and the text explains:

```text
This controls the onestep logger namespace only.
Sink-success logs are emitted at DEBUG from the shared runtime send path.
```

No code assertion is needed here; the failure is that the docs do not yet mention the new field.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_cli.py -k logging -v
```

Expected:

- documentation assertions still refer to the old YAML shape

- [ ] **Step 3: Write minimal implementation**

Update the YAML docs and README examples to show:

- `app.logging.level`
- that it only scopes the `onestep` namespace
- that DEBUG enables sink-success logging
- that this is not a handler/formatter configuration system

Keep the text short and place it near the existing “app” and “Strict Check” guidance.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/test_cli.py -k logging -v
```

Expected:

- updated docs and examples match the new YAML contract

- [ ] **Step 5: Commit**

```bash
git add docs/yaml-task-definition.md README.md
git commit -m "docs: describe yaml logging level support"
```

### Task 4: Verify the full flow end to end

**Files:**
- Test: `tests/test_cli.py`
- Test: `tests/contract/test_runtime_contract.py`
- Test: `tests/test_http_sink.py`

- [ ] **Step 1: Write the failing end-to-end test**

Add one YAML-driven test that loads:

```python
{
    "apiVersion": "onestep/v1alpha1",
    "kind": "App",
    "app": {
        "name": "yaml-end-to-end",
        "logging": {"level": "DEBUG"},
    },
    "resources": {
        "incoming": {"type": "memory"},
        "processed": {"type": "memory"},
    },
    "tasks": [
        {
            "name": "forward",
            "source": "incoming",
            "emit": "processed",
        }
    ],
}
```

Then run one item through `app.run_task_once(...)` and assert:

```python
await app.run_task_once("forward", payload={"value": 1})
```

produces both:

```python
task succeeded
sink send succeeded
```

in the captured logs at the expected levels.

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
pytest tests/test_cli.py tests/contract/test_runtime_contract.py tests/test_http_sink.py -k logging -v
```

Expected:

- one or more assertions still fail because the new config and sink logging are not fully wired together

- [ ] **Step 3: Write minimal implementation**

Fix any remaining wiring gaps only if the earlier tasks missed them. Do not add new logging APIs or schema fields.

- [ ] **Step 4: Run test to verify it passes**

Run:

```bash
pytest tests/test_cli.py tests/contract/test_runtime_contract.py tests/test_http_sink.py -k logging -v
```

Expected:

- the YAML-configured logger level enables sink-success logs
- existing HTTP sink transport behavior remains unchanged

- [ ] **Step 5: Commit**

```bash
git add tests/test_cli.py tests/contract/test_runtime_contract.py tests/test_http_sink.py
git commit -m "test: cover yaml logging level and sink success logs"
```

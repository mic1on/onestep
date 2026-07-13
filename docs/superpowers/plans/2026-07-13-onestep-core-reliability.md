# onestep Core Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the onestep core package safer to depend on by documenting its stable contracts, adding local plugin compatibility verification, and locking key runtime reliability semantics with tests.

**Architecture:** Add a dedicated reliability contract document under `docs/`, link it from both READMEs, and add one local verification script that runs core tests plus plugin tests in isolated pytest processes. Add focused tests for the script, public API exports, ack-failure duplicate semantics, multi-sink non-transactional fan-out, and non-cancel-safe fetch release behavior. Keep runtime code unchanged unless a contract test proves current behavior contradicts the intended contract.

**Tech Stack:** Python 3.9+, pytest, bash scripts, existing onestep runtime and plugin workspace.

---

## File Structure

- Create `docs/core-reliability.md`: public API tiers, runtime delivery semantics, stop-control contracts, YAML/resource contracts, plugin compatibility gate, and release checklist.
- Modify `README.md`: add a `docs/core-reliability.md` link under "More" and remove the duplicate deploy link while touching that section.
- Modify `README.zh-CN.md`: add a matching link to the English reliability contract under "更多" and remove the duplicate deploy link while touching that section.
- Create `scripts/run-reliability-checks.sh`: run `tests/` non-integration tests once, then each plugin test suite in a separate pytest process.
- Modify `Makefile`: add `reliability-test` target for the new script.
- Modify `tests/test_packaging.py`: assert stable public API names remain importable without optional connector dependencies.
- Create `tests/test_reliability_checks_script.py`: syntax and coverage checks for the reliability script.
- Modify `tests/contract/test_runtime_contract.py`: add focused contract tests for ack failure after sink send, multi-sink partial success, and non-cancel-safe fetch release.
- Update `CHANGELOG.md`: add an unreleased entry for reliability contract docs and local verification.

## Task 1: Document Core Reliability Contract

**Files:**
- Create: `docs/core-reliability.md`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Add `docs/core-reliability.md`**

Create the document with these sections:

```markdown
# Core Reliability Contract

## Status

`onestep` core provides an at-least-once async task runtime. It is intended to be stable for application code and connector plugins, but only the APIs listed in this document should be treated as compatibility commitments.

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

Connector packages may rely on these names when implementing resource handlers or normalizing backend failures.

## Operational API

- `ControlPlaneReporter`
- `ControlPlaneReporterConfig`
- identity helpers exported from `onestep`
- low-level WebSocket transport classes exported from `onestep`

These APIs are supported for operational integration, but changes may require coordination with `onestep-control-plane`.

## Internal API

Anything not listed above should be treated as internal even when it can be imported by module path.

## Delivery Semantics

`onestep` is at-least-once. It does not provide exactly-once delivery.

For a successful delivery, the runtime:

1. calls `delivery.start_processing()`
2. invokes the task handler
3. runs success hooks
4. sends the handler result to selected sinks
5. calls `delivery.ack()`
6. emits a success event

Because sink sends happen before `ack()`, a process crash or `ack()` failure after a successful sink send can produce duplicate downstream output. Production handlers and sinks should be idempotent when duplicate output matters.

Multi-sink fan-out is not transactional. If an earlier sink succeeds and a later sink fails, the earlier send is not rolled back. A retry may send to the earlier sink again.

## Failure Semantics

- Handler, predicate, hook, sink, and timeout failures enter the task retry/dead-letter path.
- Retry policies decide whether the delivery is retried or failed.
- Dead-letter publish failure retries the original delivery.
- Delivery `fail()` failure falls back to retrying the original delivery.
- Cancellation retries the delivery.

## Stop Controls

- `drain` stops new fetches and waits for inflight deliveries to finish.
- `pause_task` stops new fetches for one task and waits for that task's inflight deliveries.
- `resume_task` lets a paused task fetch again.
- Shutdown waits for inflight work up to `shutdown_timeout_s`.
- Sources that cannot safely cancel a fetch should set `fetch_is_cancel_safe = False` and implement `release_unstarted()` on deliveries that have been claimed but not processed.

## YAML And Resource Contracts

- Strict YAML mode rejects unknown fields.
- Resource handlers own strict field validation for plugin resource types.
- `app.strict_env` catches missing environment variables without defaults.
- YAML remains a wiring layer, not a workflow DSL.
- Plugin resources register through the `onestep.resources` entry point group.

## Local Reliability Verification

Run core non-integration tests plus every plugin suite in isolated pytest processes:

```bash
./scripts/run-reliability-checks.sh
```

This avoids pytest module-name collisions between plugin test suites while preserving the same coverage expected from local core changes.

## Release Checklist

Before releasing core, answer:

- Does this change affect stable application APIs?
- Does this change affect stable plugin APIs?
- Does this change alter delivery, retry, dead-letter, drain, pause, resume, shutdown, or cancellation semantics?
- Does this change alter reporter payloads, topology fields, task lifecycle events, runtime identity, remote-control behavior, or WebSocket protocol behavior?
- Do any plugins need a lower-bound dependency update such as `onestep>=<new-version>`?
- Does `CHANGELOG.md` describe the user-visible behavior and compatibility impact?
- Does `onestep-control-plane` need coordination?
```

- [ ] **Step 2: Link the contract from both READMEs**

In `README.md` under `## More`, include:

```markdown
- [`docs/core-reliability.md`](docs/core-reliability.md) — stable API,
  delivery semantics, plugin compatibility, and release checklist
```

In `README.zh-CN.md` under `## 更多`, include:

```markdown
- [`docs/core-reliability.md`](docs/core-reliability.md) —— 稳定 API、
  交付语义、插件兼容性与发布检查清单
```

While editing those lists, remove the duplicated `deploy/` entry.

- [ ] **Step 3: Update changelog**

Under `## Unreleased` in `CHANGELOG.md`, add:

```markdown
- Documents the core reliability contract, including stable API tiers, at-least-once delivery semantics, plugin compatibility checks, and core release governance.
- Adds a local reliability check command for core and plugin compatibility verification.
```

- [ ] **Step 4: Review the docs**

Run:

```bash
sed -n '1,260p' docs/core-reliability.md
sed -n '205,240p' README.md
sed -n '205,240p' README.zh-CN.md
```

Expected: the new contract is readable, both READMEs link to it, and the duplicated deploy link is gone.

## Task 2: Add Local Reliability Check Script

**Files:**
- Create: `scripts/run-reliability-checks.sh`
- Modify: `Makefile`
- Test: `tests/test_reliability_checks_script.py`

- [ ] **Step 1: Write script tests**

Create `tests/test_reliability_checks_script.py`:

```python
from __future__ import annotations

import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-reliability-checks.sh"


def test_reliability_check_script_is_executable_and_valid_bash() -> None:
    assert SCRIPT.exists()
    assert os.access(SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_reliability_check_script_runs_plugin_suites_in_isolated_processes() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert '"$PYTHON_BIN" -m pytest -q -m "not integration" tests "$@"' in text
    for plugin in (
        "plugins/onestep-feishu-bitable/tests",
        "plugins/onestep-mysql/tests",
        "plugins/onestep-postgres/tests",
        "plugins/onestep-rabbitmq/tests",
        "plugins/onestep-redis/tests",
        "plugins/onestep-sqs/tests",
    ):
        assert plugin in text
    assert 'for path in "${plugin_paths[@]}"' in text
    assert '"$PYTHON_BIN" -m pytest -q -m "not integration" "$path" "$@"' in text
```

- [ ] **Step 2: Run script tests and verify they fail**

Run:

```bash
uv run pytest tests/test_reliability_checks_script.py -q
```

Expected: fail because `scripts/run-reliability-checks.sh` does not exist yet.

- [ ] **Step 3: Add `scripts/run-reliability-checks.sh`**

Create the script:

```bash
#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${ONESTEP_PYTHON_BIN:-$ROOT_DIR/.venv/bin/python}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="${ONESTEP_PYTHON_BIN:-python3}"
fi

cd "$ROOT_DIR"

echo "==> Running core non-integration tests"
"$PYTHON_BIN" -m pytest -q -m "not integration" tests "$@"

plugin_paths=(
  "plugins/onestep-feishu-bitable/tests"
  "plugins/onestep-mysql/tests"
  "plugins/onestep-postgres/tests"
  "plugins/onestep-rabbitmq/tests"
  "plugins/onestep-redis/tests"
  "plugins/onestep-sqs/tests"
)

echo "==> Running plugin non-integration tests in isolated pytest processes"
for path in "${plugin_paths[@]}"; do
  if find "$ROOT_DIR/$path" -maxdepth 1 -type f -name 'test_*.py' | grep -q .; then
    echo "==> $path"
    "$PYTHON_BIN" -m pytest -q -m "not integration" "$path" "$@"
  else
    echo "==> $path (no tests found, skipped)"
  fi
done
```

Then run:

```bash
chmod +x scripts/run-reliability-checks.sh
```

- [ ] **Step 4: Add Makefile target**

Change the `.PHONY` line to include `reliability-test` and add:

```make
reliability-test:
	./scripts/run-reliability-checks.sh
```

- [ ] **Step 5: Run script tests and syntax checks**

Run:

```bash
uv run pytest tests/test_reliability_checks_script.py -q
```

Expected: pass.

## Task 3: Add Core Contract Tests

**Files:**
- Modify: `tests/test_packaging.py`
- Modify: `tests/contract/test_runtime_contract.py`

- [ ] **Step 1: Add stable public API import test**

Append this test to `tests/test_packaging.py`:

```python
def test_stable_core_api_exports_are_importable_without_optional_dependencies() -> None:
    script = """
from onestep import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
    CronSource,
    Delivery,
    Envelope,
    HttpSink,
    InMemoryMetrics,
    IntervalSource,
    MaxAttempts,
    MemoryQueue,
    NoRetry,
    OneStepApp,
    ResourceBuildContext,
    ResourceRegistry,
    ResourceSpecHandler,
    ResourceValidationContext,
    RetryAction,
    RetryDecision,
    Sink,
    Source,
    StructuredEventLogger,
    TaskContext,
    TaskEvent,
    TaskEventKind,
    WebhookSource,
    load_resource_plugins,
    register_resource_type,
)

assert OneStepApp("api").name == "api"
assert MemoryQueue("api.queue").name == "api.queue"
assert RetryDecision.RETRY.value == "retry"
assert ConnectorOperation.FETCH.value == "fetch"
assert callable(load_resource_plugins)
assert callable(register_resource_type)
"""
    repo_root = PYPROJECT_PATH.parent
    env = {
        **os.environ,
        "PYTHONPATH": str(repo_root / "src"),
    }
    subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        cwd=repo_root,
        env=env,
    )
```

- [ ] **Step 2: Add runtime helper classes**

Add these helper classes near the existing test helper classes in `tests/contract/test_runtime_contract.py`:

```python
class _AckFailsOnceDelivery(Delivery):
    def __init__(self, source: "_AckFailsOnceSource", envelope: Envelope) -> None:
        super().__init__(envelope)
        self._source = source

    async def ack(self) -> None:
        self._source.ack_calls += 1
        if self.envelope.attempts == 0:
            raise RuntimeError("ack failed after sink send")
        self._source.acked_attempts.append(self.envelope.attempts)

    async def retry(self, *, delay_s: float | None = None) -> None:
        self._source.retry_calls += 1
        if delay_s:
            await asyncio.sleep(delay_s)
        await self._source.put(
            Envelope(
                body=self.envelope.body,
                meta=dict(self.envelope.meta),
                attempts=self.envelope.attempts + 1,
            )
        )

    async def fail(self, exc: Exception | None = None) -> None:
        self._source.fail_calls += 1


class _AckFailsOnceSource(Source):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.poll_interval_s = 0.01
        self._queue: asyncio.Queue[Envelope] = asyncio.Queue()
        self.ack_calls = 0
        self.retry_calls = 0
        self.fail_calls = 0
        self.acked_attempts: list[int] = []

    async def put(self, envelope: Envelope) -> None:
        await self._queue.put(envelope)

    async def fetch(self, limit: int) -> list[Delivery]:
        try:
            envelope = await asyncio.wait_for(self._queue.get(), timeout=self.poll_interval_s)
        except asyncio.TimeoutError:
            return []
        return [_AckFailsOnceDelivery(self, envelope)]


class _AlwaysFailSink(Sink):
    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.calls = 0

    async def send(self, envelope: Envelope) -> None:
        self.calls += 1
        raise RuntimeError("sink failed after previous sink succeeded")


class _ReleaseTrackingDelivery(Delivery):
    def __init__(self, envelope: Envelope) -> None:
        super().__init__(envelope)
        self.released = False
        self.acked = False
        self.retried = False
        self.failed = False

    async def release_unstarted(self) -> None:
        self.released = True

    async def ack(self) -> None:
        self.acked = True

    async def retry(self, *, delay_s: float | None = None) -> None:
        self.retried = True

    async def fail(self, exc: Exception | None = None) -> None:
        self.failed = True


class _NonCancelSafeSource(Source):
    fetch_is_cancel_safe = False

    def __init__(self, name: str) -> None:
        super().__init__(name)
        self.poll_interval_s = 0.01
        self.fetch_started = asyncio.Event()
        self.release_fetch = asyncio.Event()
        self.delivery = _ReleaseTrackingDelivery(Envelope(body={"value": 1}))

    async def fetch(self, limit: int) -> list[Delivery]:
        self.fetch_started.set()
        await self.release_fetch.wait()
        return [self.delivery]
```

- [ ] **Step 3: Add ack-failure at-least-once contract test**

Add this test near the existing retry and sink tests:

```python
def test_ack_failure_after_sink_send_retries_and_may_duplicate_output_contract() -> None:
    async def scenario() -> None:
        source = _AckFailsOnceSource("ack-fails")
        sink = MemoryQueue("processed", poll_interval_s=0.01)
        app = OneStepApp("ack-failure-contract")
        attempts: list[int] = []

        @app.task(source=source, emit=sink, retry=MaxAttempts(2, delay_s=0))
        async def consume(ctx, item):
            attempts.append(ctx.current.attempts)
            if ctx.current.attempts == 1:
                ctx.app.request_shutdown()
            return {"value": item["value"], "attempt": ctx.current.attempts}

        await source.put(Envelope(body={"value": 3}))
        await asyncio.wait_for(app.serve(), timeout=1.0)

        first = await sink.fetch(1)
        second = await sink.fetch(1)

        assert attempts == [0, 1]
        assert source.ack_calls == 2
        assert source.retry_calls == 1
        assert source.fail_calls == 0
        assert source.acked_attempts == [1]
        assert [delivery.payload for delivery in [*first, *second]] == [
            {"value": 3, "attempt": 0},
            {"value": 3, "attempt": 1},
        ]

    asyncio.run(scenario())
```

- [ ] **Step 4: Add multi-sink non-transactional contract test**

Add this test near existing emit contract tests:

```python
def test_multi_sink_send_is_not_transactional_when_later_sink_fails_contract() -> None:
    async def scenario() -> None:
        source = MemoryQueue("incoming", poll_interval_s=0.01)
        first_sink = MemoryQueue("first", poll_interval_s=0.01)
        failing_sink = _AlwaysFailSink("second")
        app = OneStepApp("multi-sink-contract")

        @app.task(source=source, emit=[first_sink, failing_sink], retry=NoRetry())
        async def consume(ctx, item):
            ctx.app.request_shutdown()
            return {"value": item["value"] + 1}

        await source.publish({"value": 1})
        await asyncio.wait_for(app.serve(), timeout=1.0)

        first_batch = await first_sink.fetch(1)
        assert failing_sink.calls == 1
        assert [delivery.payload for delivery in first_batch] == [{"value": 2}]

    asyncio.run(scenario())
```

- [ ] **Step 5: Add non-cancel-safe fetch release contract test**

Add this test near existing drain and pause tests:

```python
def test_non_cancel_safe_fetch_releases_unstarted_deliveries_on_drain_contract() -> None:
    async def scenario() -> None:
        source = _NonCancelSafeSource("non-cancel-safe")
        app = OneStepApp("non-cancel-safe-contract")
        seen: list[dict[str, int]] = []

        @app.task(source=source)
        async def consume(ctx, item):
            seen.append(item)

        serve_task = asyncio.create_task(app.serve())
        await asyncio.wait_for(source.fetch_started.wait(), timeout=1.0)

        app.request_drain()
        source.release_fetch.set()
        drain_status = await asyncio.wait_for(app.wait_for_drain(), timeout=1.0)

        assert drain_status["drained"] is True
        assert seen == []
        assert source.delivery.released is True
        assert source.delivery.acked is False
        assert source.delivery.retried is False
        assert source.delivery.failed is False

        app.request_shutdown()
        await asyncio.wait_for(serve_task, timeout=1.0)

    asyncio.run(scenario())
```

- [ ] **Step 6: Run focused tests**

Run:

```bash
uv run pytest tests/test_packaging.py tests/contract/test_runtime_contract.py -q -k "stable_core_api or ack_failure or multi_sink_send or non_cancel_safe"
```

Expected: pass.

## Task 4: Run Full Reliability Verification

**Files:**
- Verify: all modified files

- [ ] **Step 1: Run the core non-integration suite**

Run:

```bash
uv run pytest -q -m "not integration"
```

Expected: all tests pass.

- [ ] **Step 2: Run the new reliability command**

Run:

```bash
./scripts/run-reliability-checks.sh
```

Expected: core non-integration tests pass, then each plugin suite passes in its own pytest invocation without import mismatch.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
git diff --stat
git diff -- docs/core-reliability.md README.md README.zh-CN.md CHANGELOG.md Makefile scripts/run-reliability-checks.sh tests/test_packaging.py tests/test_reliability_checks_script.py tests/contract/test_runtime_contract.py
```

Expected: only the planned files changed, with no runtime implementation changes unless a test exposed a contract mismatch.

- [ ] **Step 4: Commit implementation**

Run:

```bash
git add docs/core-reliability.md README.md README.zh-CN.md CHANGELOG.md Makefile scripts/run-reliability-checks.sh tests/test_packaging.py tests/test_reliability_checks_script.py tests/contract/test_runtime_contract.py docs/superpowers/plans/2026-07-13-onestep-core-reliability.md
git commit -m "docs: codify onestep core reliability contract"
```

Expected: one commit containing the reliability contract, local verification script, and focused tests.


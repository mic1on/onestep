# PostgreSQL Execution Backend 实施计划

> **面向执行 Agent：** 实施时必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐项完成。步骤使用复选框（`- [ ]`）跟踪。

**目标：** 为 FastAPI 和独立 onestep worker 提供 `step.submit/get/list/cancel/result`，由 PostgreSQL 持久化任务、状态、租约、attempt 和结果，同时保持所有普通 Delivery 行为兼容。

**架构：** core 只增加 backend/client/快照模型和可选 `ManagedExecutionDelivery` 协议；`onestep-postgres` 负责 schema、CRUD、`SKIP LOCKED` 领取、租约、心跳与 fencing。Worker 继续走现有 `Source -> DeliveryExecutor`，FastAPI 与 worker 通过同一 PostgreSQL 表解耦。

**技术栈：** Python 3.9+、asyncio、onestep Source/Delivery runtime、SQLAlchemy 2.x 同步 Engine + `asyncio.to_thread`、psycopg 3、PostgreSQL 16、pytest、Docker Compose、uv。

---

## 开工约束

- 先阅读设计规格：`docs/superpowers/specs/2026-08-09-postgres-execution-backend-design.md`。
- 不重构现有 PostgreSQL table queue、incremental、sink 或 state store。
- 不迁移整个 PostgreSQL 插件到 AsyncEngine；本计划沿用当前同步 Engine + `asyncio.to_thread`，降低范围。
- 不修改 `Delivery.ack/retry/fail` 签名，不给 `Envelope` 增加必填字段。
- 每个任务完成后运行指定测试并做独立提交；测试未通过时不进入下一任务。
- 版本在最后一个任务统一升级，避免中途提交声明尚未可用的发布版本。

## 文件结构

### 核心包

- Create `src/onestep/execution.py`: public execution models, errors, backend protocol, client, managed completion protocol.
- Modify `src/onestep/runtime/executor.py`: route success/retry/failure/cancellation through the optional managed protocol.
- Modify `src/onestep/__init__.py`: export stable business and plugin-facing execution symbols.
- Create `tests/test_execution.py`: client/model/validation/result behavior.
- Modify `tests/contract/test_runtime_contract.py`: ordinary-delivery compatibility and managed-delivery lifecycle contracts.
- Modify `tests/test_packaging.py`: verify core-only public imports without PostgreSQL installed.

### PostgreSQL 插件

- Create `plugins/onestep-postgres/src/onestep_postgres/execution_schema.py`: SQLAlchemy table and index definitions.
- Create `plugins/onestep-postgres/src/onestep_postgres/execution_backend.py`: CRUD, idempotency, pagination, cancellation, claim, heartbeat, fencing transitions.
- Create `plugins/onestep-postgres/src/onestep_postgres/execution_source.py`: Source, Delivery, heartbeat task and cooperative cancellation.
- Modify `plugins/onestep-postgres/src/onestep_postgres/connector.py`: add `execution_backend()` factory.
- Modify `plugins/onestep-postgres/src/onestep_postgres/resources.py`: add strict `postgres_execution_source` resource.
- Modify `plugins/onestep-postgres/src/onestep_postgres/__init__.py`: export new plugin API.
- Create `plugins/onestep-postgres/tests/test_postgres_execution_backend.py`: SQLite CRUD/state/idempotency/page tests.
- Create `plugins/onestep-postgres/tests/test_postgres_execution_source.py`: fake-clock claim/lease/heartbeat/delivery tests.
- Modify `plugins/onestep-postgres/tests/test_postgres_plugin.py`: catalog and strict YAML tests.
- Create `plugins/onestep-postgres/tests/integration/test_postgres_execution_live.py`: real PostgreSQL concurrency and fencing tests.

### 集成、文档与发布

- Modify `docker-compose.integration.yml`: add PostgreSQL service.
- Modify `scripts/setup-integration-env.sh`: wait for PostgreSQL and export `ONESTEP_POSTGRES_DSN`.
- Modify `scripts/run-integration-tests.sh`: include PostgreSQL integration tests.
- Modify `tests/test_database_plugin_integration.py`: lock the PostgreSQL live harness into repository contracts.
- Modify `.github/workflows/plugin-postgres.yml`: run live PostgreSQL tests and gate publishing on them.
- Modify `deploy/web-service-integration.md`: document FastAPI submit/query and separate-process deployment.
- Modify `docs/broker/postgres.md`: document execution backend Python/YAML usage and guarantees.
- Modify `plugins/onestep-postgres/README.md`: concise install and execution examples.
- Modify `skills/onestep/references/python-api.md`: add `ExecutionClient` API.
- Modify `skills/onestep/references/connectors.md`: add PostgreSQL execution source wiring.
- Modify `CHANGELOG.md`, `pyproject.toml`, `plugins/onestep-postgres/pyproject.toml`, and `uv.lock`: release metadata.

## 任务 1：添加核心执行模型与客户端

**文件：**

- Create: `src/onestep/execution.py`
- Create: `tests/test_execution.py`
- Modify: `src/onestep/__init__.py`
- Modify: `tests/test_packaging.py`

- [ ] **步骤 1：编写模型与客户端失败测试**

Create `tests/test_execution.py` with a backend double that records typed requests and returns frozen snapshots. Cover submit, namespace binding, list forwarding, idempotent cancel, successful `None` result, and all typed result exceptions.

The first test slice must include these assertions:

```python
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from onestep import (
    Execution,
    ExecutionCancelled,
    ExecutionClient,
    ExecutionError,
    ExecutionExpired,
    ExecutionFailed,
    ExecutionNotFound,
    ExecutionNotReady,
    ExecutionPage,
    ExecutionStatus,
)


NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


def snapshot(
    status: ExecutionStatus,
    *,
    execution_id: UUID | None = None,
    result: object | None = None,
    error: ExecutionError | None = None,
) -> Execution:
    return Execution(
        id=execution_id or uuid4(),
        namespace="agent-api",
        task_name="run_agent",
        status=status,
        payload={"prompt": "hello"},
        metadata={"requested_by": "u-1"},
        result=result,
        error=error,
        attempts=0,
        created_at=NOW,
        available_at=NOW,
        started_at=None,
        finished_at=None,
        cancel_requested_at=None,
        expires_at=None,
        version=0,
    )


class FakeBackend:
    def __init__(self, current: Execution | None) -> None:
        self.current = current
        self.submissions = []
        self.queries = []
        self.cancel_requests = []

    async def submit(self, request):
        self.submissions.append(request)
        assert self.current is not None
        return self.current

    async def get(self, namespace, execution_id):
        self.queries.append((namespace, execution_id))
        return self.current

    async def list(self, query):
        self.queries.append(query)
        return ExecutionPage(items=(() if self.current is None else (self.current,)), next_cursor=None)

    async def request_cancel(self, namespace, execution_id, *, reason):
        self.cancel_requests.append((namespace, execution_id, reason))
        return self.current


def test_submit_binds_namespace_and_returns_frozen_snapshot() -> None:
    async def scenario() -> None:
        queued = snapshot(ExecutionStatus.QUEUED)
        backend = FakeBackend(queued)
        step = ExecutionClient(backend, namespace="agent-api")

        actual = await step.submit(
            "run_agent",
            {"prompt": "hello"},
            idempotency_key="request-1",
            metadata={"requested_by": "u-1"},
        )

        assert actual is queued
        request = backend.submissions[0]
        assert request.namespace == "agent-api"
        assert request.task_name == "run_agent"
        assert request.idempotency_key == "request-1"
        with pytest.raises(AttributeError):
            actual.status = ExecutionStatus.RUNNING

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (ExecutionStatus.QUEUED, ExecutionNotReady),
        (ExecutionStatus.RUNNING, ExecutionNotReady),
        (ExecutionStatus.RETRYING, ExecutionNotReady),
        (ExecutionStatus.CANCEL_REQUESTED, ExecutionNotReady),
        (ExecutionStatus.FAILED, ExecutionFailed),
        (ExecutionStatus.CANCELLED, ExecutionCancelled),
        (ExecutionStatus.EXPIRED, ExecutionExpired),
    ],
)
def test_result_raises_by_status(status, error_type) -> None:
    async def scenario() -> None:
        error = ExecutionError(kind="error", exception_type="ValueError")
        step = ExecutionClient(FakeBackend(snapshot(status, error=error)), namespace="agent-api")
        with pytest.raises(error_type):
            await step.result(uuid4())

    asyncio.run(scenario())


def test_result_distinguishes_missing_from_successful_none() -> None:
    async def scenario() -> None:
        execution_id = uuid4()
        missing = ExecutionClient(FakeBackend(None), namespace="agent-api")
        with pytest.raises(ExecutionNotFound):
            await missing.result(execution_id)

        succeeded = ExecutionClient(
            FakeBackend(snapshot(ExecutionStatus.SUCCEEDED, execution_id=execution_id, result=None)),
            namespace="agent-api",
        )
        assert await succeeded.result(execution_id) is None

    asyncio.run(scenario())
```

Add validation tests for empty namespace/task/idempotency key, negative `delay_s`, naive `expires_at`, list limit `0` and `201`, malformed UUID, and mutation of the original payload after submit.

- [ ] **步骤 2：运行聚焦测试并确认缺失 API**

Run:

```bash
uv run pytest -q tests/test_execution.py
```

Expected: collection fails because `Execution`, `ExecutionClient`, statuses and exceptions are not exported.

- [ ] **步骤 3：实现公开领域模型与协议**

Create `src/onestep/execution.py` with these exact public names:

```python
ExecutionStatus
ExecutionError
Execution
ExecutionPage
ExecutionRequest
ExecutionQuery
ExecutionBackend
ExecutionCompletion
ManagedExecutionDelivery
ExecutionClient
ExecutionNotFound
ExecutionNotReady
ExecutionFailed
ExecutionCancelled
ExecutionExpired
ExecutionConflict
ExecutionEncodingError
```

Use these status sets and completion validation:

```python
TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
        ExecutionStatus.EXPIRED,
    }
)

COMPLETION_STATUSES = frozenset(
    {
        ExecutionStatus.SUCCEEDED,
        ExecutionStatus.RETRYING,
        ExecutionStatus.FAILED,
        ExecutionStatus.CANCELLED,
    }
)
```

`Execution`, `ExecutionError`, `ExecutionPage`, `ExecutionRequest`, `ExecutionQuery` and `ExecutionCompletion` must be `@dataclass(frozen=True)`. In each model, copy mapping and payload values at the boundary with `copy.deepcopy`; convert page items to tuple. `Execution.terminal` must be a property backed by `TERMINAL_EXECUTION_STATUSES`.

Implement the client method behavior exactly as follows:

```python
async def result(self, execution_id: UUID | str) -> object:
    execution = await self.get(execution_id)
    if execution is None:
        raise ExecutionNotFound(execution_id)
    if execution.status is ExecutionStatus.SUCCEEDED:
        return copy.deepcopy(execution.result)
    if execution.status is ExecutionStatus.FAILED:
        raise ExecutionFailed(execution)
    if execution.status is ExecutionStatus.CANCELLED:
        raise ExecutionCancelled(execution)
    if execution.status is ExecutionStatus.EXPIRED:
        raise ExecutionExpired(execution)
    raise ExecutionNotReady(execution)
```

Validation rules:

| Field | Rule |
| --- | --- |
| namespace/task/idempotency | strip; non-empty; max 255 characters |
| cancel reason | strip; empty becomes `None`; max 500 characters |
| delay | `None` or finite `>= 0` |
| expires_at | `None` or timezone-aware datetime |
| list limit | integer `1..200`; booleans rejected |
| cursor | `None` or non-empty string |
| execution ID | parse with `UUID(str(value))`; raise `ValueError` on malformed input |

`ManagedExecutionDelivery` must be decorated with `@runtime_checkable` and contain the three IDs/flags plus `complete_execution()`, matching the design spec. Do not add methods to `Delivery`.

- [ ] **步骤 4：导出 API 并锁定仅安装核心包时的打包行为**

Add business-facing and plugin-facing names to `src/onestep/__init__.py` and `_CORE_EXPORTS`. Extend the subprocess import block in `tests/test_packaging.py` so a core-only environment imports at least:

```python
from onestep import (
    Execution,
    ExecutionBackend,
    ExecutionClient,
    ExecutionCompletion,
    ExecutionStatus,
    ManagedExecutionDelivery,
)

assert Execution is not None
assert ExecutionBackend is not None
assert ExecutionClient is not None
assert ExecutionCompletion is not None
assert ExecutionStatus.QUEUED.value == "queued"
assert ManagedExecutionDelivery is not None
```

No SQLAlchemy or `onestep_postgres` import may appear in core modules.

- [ ] **步骤 5：运行聚焦测试**

Run:

```bash
uv run pytest -q tests/test_execution.py tests/test_packaging.py
```

Expected: all tests pass.

- [ ] **步骤 6：提交核心公开 API**

```bash
git add src/onestep/execution.py src/onestep/__init__.py tests/test_execution.py tests/test_packaging.py
git commit -m "feat: add execution client API"
```

## 任务 2：添加受管执行的运行时语义

**文件：**

- Modify: `src/onestep/runtime/executor.py`
- Modify: `tests/contract/test_runtime_contract.py`

- [ ] **步骤 1：编写受管 Delivery 失败契约测试**

Add a test-only delivery that implements both the existing abstract methods and the optional protocol:

```python
class RecordingManagedDelivery(Delivery):
    def __init__(self, payload, steps, *, cancel_requested=False):
        super().__init__(
            Envelope(
                body=payload,
                meta={
                    "onestep.execution": {
                        "id": "00000000-0000-0000-0000-000000000001",
                        "attempt_id": "00000000-0000-0000-0000-000000000002",
                    }
                },
            )
        )
        self.execution_id = UUID("00000000-0000-0000-0000-000000000001")
        self.attempt_id = UUID("00000000-0000-0000-0000-000000000002")
        self.cancel_requested = cancel_requested
        self.steps = steps
        self.completions = []

    async def ack(self):
        self.steps.append("legacy:ack")

    async def retry(self, *, delay_s=None):
        self.steps.append("legacy:retry")

    async def fail(self, exc=None):
        self.steps.append("legacy:fail")

    async def complete_execution(self, completion):
        self.completions.append(completion)
        self.steps.append(f"managed:{completion.status.value}")
```

Add these contracts:

1. Success order is `handler -> sink -> managed:succeeded -> event:succeeded`; none of the three `legacy:*` methods is called, and completion contains the handler result.
2. A `MaxAttempts(max_attempts=2, delay_s=3)` retry creates `retrying` completion with delay `3`, a public error and no traceback.
3. Terminal handler failure creates `failed` completion.
4. Dead-letter publish failure creates `retrying` completion, preserving existing dead-letter behavior.
5. `cancel_requested=True` plus `CancelledError` creates `cancelled`; `False` creates `retrying` with immediate availability.
6. The existing `test_single_delivery_success_order_is_stable_contract` remains byte-for-byte expected as `start_processing -> started -> hooks -> handler -> sink -> ack -> succeeded`.
7. TaskEvent meta includes the nested `onestep.execution` correlation without a new event kind.

- [ ] **步骤 2：运行运行时契约并确认旧方法仍被调用**

Run:

```bash
uv run pytest -q tests/contract/test_runtime_contract.py -k "managed_execution or single_delivery_success_order"
```

Expected: new managed tests fail because `DeliveryExecutor` still calls `ack/retry/fail`.

- [ ] **步骤 3：添加成功完成辅助方法**

In `DeliveryExecutor`, add a private protocol check and route only the success action:

```python
def _managed_delivery(self, delivery: Delivery) -> ManagedExecutionDelivery | None:
    if isinstance(delivery, ManagedExecutionDelivery):
        return delivery
    return None


async def _apply_success(self, delivery: Delivery, result: Any) -> None:
    managed = self._managed_delivery(delivery)
    if managed is None:
        await delivery.ack()
        return
    await managed.complete_execution(
        ExecutionCompletion(
            status=ExecutionStatus.SUCCEEDED,
            result=copy.deepcopy(result),
        )
    )
```

Replace only the existing `await delivery.ack()` call with `await self._apply_success(delivery, outcome.handler_result)`. Preserve checkpoints, `DeliveryAction.ACK`, sink ordering and success event ordering.

- [ ] **步骤 4：通过受管完成协议处理重试与终态失败**

Change `_apply_retry` to accept an optional `ExecutionError`, and use:

```python
managed = self._managed_delivery(delivery)
if managed is None:
    await delivery.retry(delay_s=delay_s)
else:
    await managed.complete_execution(
        ExecutionCompletion(
            status=ExecutionStatus.RETRYING,
            error=error,
            delay_s=delay_s,
        )
    )
```

Change `_fail_delivery` analogously. For managed delivery, call `complete_execution()` with `status=ExecutionStatus.FAILED` and the sanitized `ExecutionError`; if that raises, call managed retry completion with the same error, not legacy `delivery.retry()`.

Build the public error from the already sanitized `outcome.public_failure` plus `outcome.failure_stage`. Persist only `kind`, `exception_type`, `stage`, `backend`, `operation`, and `connector_kind`; do not pass `FailureInfo.message` or traceback.

Update every `_apply_retry` and `_fail_delivery` call site, including dead-letter failure, so the same sanitized error reaches the backend.

- [ ] **步骤 5：区分业务取消与 worker 取消**

In `_handle_cancelled`, select completion by `managed.cancel_requested`:

```python
if managed is not None and managed.cancel_requested:
    status = ExecutionStatus.CANCELLED
else:
    status = ExecutionStatus.RETRYING
```

For a managed delivery, submit that completion and emit the existing `CANCELLED` event. Emit `RETRIED` only when the selected status is `retrying`. Continue re-raising `CancelledError` from `execute()` so TaskRunner task cancellation semantics remain unchanged.

- [ ] **步骤 6：运行聚焦测试和完整核心契约测试**

Run:

```bash
uv run pytest -q tests/test_execution.py tests/contract/test_runtime_contract.py tests/test_diagnostics.py tests/test_failure_capture.py
```

Expected: all tests pass; existing ordinary-delivery order assertions are unchanged.

- [ ] **步骤 7：提交运行时扩展**

```bash
git add src/onestep/runtime/executor.py tests/contract/test_runtime_contract.py
git commit -m "feat: support managed execution deliveries"
```

## 任务 3：实现 PostgreSQL Schema 与业务 CRUD

**文件：**

- Create: `plugins/onestep-postgres/src/onestep_postgres/execution_schema.py`
- Create: `plugins/onestep-postgres/src/onestep_postgres/execution_backend.py`
- Create: `plugins/onestep-postgres/tests/test_postgres_execution_backend.py`

- [ ] **步骤 1：编写 CRUD、编码与幂等失败测试**

Build each test with a temporary SQLite database through the existing `PostgresConnector`; SQLite is acceptable for deterministic CRUD but not for claim-concurrency proof.

Required tests:

```text
test_submit_get_and_successful_none_result_round_trip
test_submit_rejects_payload_over_one_mib
test_submit_tagged_codec_round_trips_uuid_datetime_decimal_and_bytes
test_same_idempotency_key_and_digest_returns_original_execution
test_same_idempotency_key_with_different_payload_raises_conflict
test_cancel_queued_and_retrying_becomes_cancelled
test_cancel_running_becomes_cancel_requested
test_cancel_terminal_is_idempotent
test_list_filters_task_and_status_with_keyset_cursor
test_list_rejects_cursor_with_unknown_version
test_open_without_auto_create_reports_missing_tables
```

The idempotency test must call two separate backend instances against the same file and assert one execution ID. The pagination test inserts at least four executions with tied timestamps and proves there are no duplicates or omissions across pages.

- [ ] **步骤 2：运行 backend 测试并确认缺失模块**

Run:

```bash
uv run --all-packages pytest -q plugins/onestep-postgres/tests/test_postgres_execution_backend.py
```

Expected: collection fails because `PostgresExecutionBackend` and schema modules do not exist.

- [ ] **步骤 3：定义 schema 元数据**

In `execution_schema.py`, implement:

```python
@dataclass(frozen=True)
class ExecutionTables:
    metadata: sa.MetaData
    executions: sa.Table
    attempts: sa.Table


def build_execution_tables(
    *,
    executions_table: str,
    attempts_table: str,
) -> ExecutionTables:
    metadata = sa.MetaData()
    executions = _build_executions_table(metadata, executions_table)
    attempts = _build_attempts_table(metadata, attempts_table, executions)
    _add_execution_indexes(executions)
    _add_attempt_indexes(attempts)
    return ExecutionTables(metadata=metadata, executions=executions, attempts=attempts)
```

Implement the four private helpers named in the snippet. They must contain every column, constraint and index from design section 11. Use SQLAlchemy portable `sa.JSON` with PostgreSQL `JSONB` variant, `sa.Uuid(as_uuid=True)`, timezone-aware `DateTime`, and dialect-specific partial-index predicates. Validate both table names with the same non-empty identifier rule before constructing metadata.

Do not reflect these tables through `PostgresConnector._table()`; execution schema is framework-owned and defined explicitly.

- [ ] **步骤 4：实现 backend 生命周期与序列化**

Create `PostgresExecutionBackend` with this constructor and public methods:

```python
class PostgresExecutionBackend:
    def __init__(
        self,
        *,
        connector: PostgresConnector,
        table: str = "onestep_executions",
        attempts_table: str = "onestep_execution_attempts",
        auto_create: bool = True,
        max_payload_bytes: int = 1024 * 1024,
        max_metadata_bytes: int = 64 * 1024,
        max_result_bytes: int = 1024 * 1024,
        clock: Callable[[], datetime] | None = None,
    ) -> None

    async def open(self) -> None
    async def submit(self, request: ExecutionRequest) -> Execution
    async def get(self, namespace: str, execution_id: UUID) -> Execution | None
    async def list(self, query: ExecutionQuery) -> ExecutionPage
    async def request_cancel(
        self,
        namespace: str,
        execution_id: UUID,
        *,
        reason: str | None,
    ) -> Execution | None
```

`clock` is an internal test seam; resource and connector factories do not expose it. It must always return aware UTC datetimes.

Use `encode_value/decode_value` for payload, metadata and result. Calculate encoded byte size with canonical JSON:

```python
json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("ascii")
```

Wrap `CaptureEncodingError` as `ExecutionEncodingError`. Never persist a partial row after encoding or size validation fails.

- [ ] **步骤 5：实现事务性幂等提交**

Create the digest from this canonical object:

```python
{
    "namespace": request.namespace,
    "task_name": request.task_name,
    "payload": encoded_payload,
    "metadata": encoded_metadata,
    "delay_s": request.delay_s,
    "expires_at": request.expires_at.isoformat() if request.expires_at else None,
}
```

On a unique-key conflict, roll back the failed insert transaction, load by `(namespace, task_name, idempotency_key)`, compare `submission_digest`, and return the existing snapshot only when digests match. Otherwise raise `ExecutionConflict`.

All public async methods call one sync transaction helper through `asyncio.to_thread`; never hold a SQLAlchemy connection across an `await`.

- [ ] **步骤 6：实现 keyset 分页查询与取消 CAS**

Cursor payload must be exactly:

```json
{"v":1,"created_at":"2026-08-09T00:00:00+00:00","id":"<uuid>"}
```

Encode compact JSON with URL-safe base64 and restore missing padding on decode. The page query order and cursor predicate are:

```sql
ORDER BY created_at DESC, id DESC
WHERE created_at < :created_at
   OR (created_at = :created_at AND id < :id)
LIMIT :limit_plus_one
```

Cancellation transaction rules:

```text
queued/retrying -> cancelled + finished_at
running         -> cancel_requested + cancel_requested_at
cancel_requested/terminal -> unchanged
missing         -> None
```

Increment `version` and `updated_at` only when a row changes.

- [ ] **步骤 7：运行 backend 测试**

Run:

```bash
uv run --all-packages pytest -q plugins/onestep-postgres/tests/test_postgres_execution_backend.py
```

Expected: all tests pass.

- [ ] **步骤 8：提交 schema 与 CRUD**

```bash
git add plugins/onestep-postgres/src/onestep_postgres/execution_schema.py plugins/onestep-postgres/src/onestep_postgres/execution_backend.py plugins/onestep-postgres/tests/test_postgres_execution_backend.py
git commit -m "feat(postgres): add execution backend storage"
```

## 任务 4：实现领取、租约、心跳与 fencing

**文件：**

- Modify: `plugins/onestep-postgres/src/onestep_postgres/execution_backend.py`
- Create: `plugins/onestep-postgres/src/onestep_postgres/execution_source.py`
- Create: `plugins/onestep-postgres/tests/test_postgres_execution_source.py`

- [ ] **步骤 1：使用伪时钟编写 backend 租约失败测试**

Use an aware mutable clock and direct backend calls. Required tests:

```text
test_claim_skips_delayed_execution_until_available_at
test_claim_sets_running_attempt_and_envelope_attempts_zero
test_claim_marks_expired_queued_execution_terminal
test_heartbeat_extends_only_matching_lease
test_expired_running_lease_creates_new_attempt_and_fences_old_token
test_expired_cancel_requested_lease_becomes_cancelled
test_complete_success_updates_execution_and_attempt_in_one_transaction
test_complete_retry_sets_available_at_and_clears_lease
test_stale_lease_cannot_complete_success_failure_or_retry
test_release_unstarted_returns_claim_to_queue
```

Assert that first DB attempt number is `1` while `Envelope.attempts` is `0`, preserving current RetryPolicy behavior.

- [ ] **步骤 2：编写 Source 与 Delivery 行为失败测试**

Required tests:

```text
test_source_fetch_returns_managed_execution_delivery_with_correlation_meta
test_delivery_start_processing_runs_heartbeat_until_completion
test_heartbeat_cancel_request_cancels_owner_task
test_worker_cancellation_without_business_cancel_completes_retrying
test_business_cancel_completes_cancelled
test_source_release_unstarted_contract
```

Use events instead of timing sleeps: inject a backend double whose heartbeat sets an event and waits on another event. Keep wall-clock timeouts at one second only as deadlock guards.

- [ ] **步骤 3：运行聚焦测试并确认缺失的 claim/source API**

Run:

```bash
uv run --all-packages pytest -q plugins/onestep-postgres/tests/test_postgres_execution_source.py
```

Expected: failures report missing `claim`, `heartbeat`, completion and source classes.

- [ ] **步骤 4：实现 backend worker 操作**

Add internal frozen records:

```python
@dataclass(frozen=True)
class ExecutionLease:
    execution: Execution
    attempt_id: UUID
    lease_token: UUID
    lease_expires_at: datetime


@dataclass(frozen=True)
class HeartbeatResult:
    lease_expires_at: datetime
    cancel_requested: bool
```

Add async methods that each delegate to one sync transaction helper:

```python
claim(namespace, task_names, limit, lease_duration_s, worker_id) -> tuple[ExecutionLease, ...]
heartbeat(execution_id, attempt_id, lease_token, lease_duration_s) -> HeartbeatResult
complete(execution_id, attempt_id, lease_token, completion) -> Execution
release(execution_id, attempt_id, lease_token) -> Execution
```

Claim order inside one transaction:

1. expire eligible queued/retrying records;
2. cancel expired `cancel_requested` records;
3. mark expired running attempts `lease_lost` and make those executions claimable;
4. select eligible rows with `FOR UPDATE SKIP LOCKED`;
5. update execution and insert attempt rows;
6. return leases after commit.

The SQLite fallback may omit effective row locking; only live PostgreSQL tests are allowed to claim concurrency correctness.

- [ ] **步骤 5：实现原子完成 CAS**

Completion status mapping must be exact:

| Completion | Execution update | Attempt update |
| --- | --- | --- |
| succeeded | result, terminal timestamps, clear lease | succeeded + finished_at |
| retrying | available_at = now + delay, clear lease | retrying + error + finished_at |
| failed | error, terminal timestamps, clear lease | failed + error + finished_at |
| cancelled | terminal timestamps, clear lease | cancelled + finished_at |

Every execution update predicate includes execution ID, current lease token and expected running status. `succeeded` only accepts `running`; cancellation request committed first must win. `retrying/failed/cancelled` accept the statuses defined by the runtime path, but no completion may overwrite another terminal status.

When the update count is zero, read the row in the same transaction and either return an already-identical terminal state or raise `StaleExecutionLease`. Do not silently accept a different token.

- [ ] **步骤 6：实现 PostgresExecutionSource 与 Delivery**

`PostgresExecutionSource` must subclass `Source`, set `fetch_is_cancel_safe = False`, and validate:

```text
task_names is non-empty and unique
batch_size >= 1
poll_interval_s > 0
lease_duration_s > 0
0 < heartbeat_interval_s <= lease_duration_s / 3
worker_id is non-empty and <= 255 chars
```

`open()` must delegate to `backend.open()` so worker startup creates or verifies the execution tables before the first fetch. `close()` is a no-op because the separately registered `PostgresConnector` owns and closes the shared SQLAlchemy Engine.

`fetch(limit)` calls backend `claim()` with `min(limit, batch_size)` and returns `PostgresExecutionDelivery` objects. Envelope body is decoded payload; metadata is the decoded user metadata plus:

```python
{
    "onestep.execution": {
        "id": str(execution.id),
        "attempt_id": str(lease.attempt_id),
    }
}
```

If user metadata already contains `onestep.execution`, reject submission before insert so framework correlation cannot be spoofed.

`PostgresExecutionDelivery` behavior:

- `start_processing()` captures `asyncio.current_task()` and starts one heartbeat task.
- heartbeat updates `cancel_requested`; when true, it cancels the owner task.
- if lease renewal cannot succeed before the current lease expiry, cancel the owner task and stop heartbeating.
- `complete_execution()` stops heartbeat before calling backend `complete()`.
- `release_unstarted()` calls backend `release()` without starting heartbeat.
- legacy `ack/retry/fail` delegate to compatible completion objects; production runtime uses `complete_execution()`.

- [ ] **步骤 7：联合运行 source、backend 与运行时契约测试**

Run:

```bash
uv run --all-packages pytest -q \
  plugins/onestep-postgres/tests/test_postgres_execution_backend.py \
  plugins/onestep-postgres/tests/test_postgres_execution_source.py \
  tests/contract/test_runtime_contract.py
```

Expected: all tests pass with no pending heartbeat tasks or asyncio warnings.

- [ ] **步骤 8：提交租约运行时**

```bash
git add plugins/onestep-postgres/src/onestep_postgres/execution_backend.py plugins/onestep-postgres/src/onestep_postgres/execution_source.py plugins/onestep-postgres/tests/test_postgres_execution_source.py
git commit -m "feat(postgres): add leased execution source"
```

## 任务 5：接入 Connector、导出与严格 YAML

**文件：**

- Modify: `plugins/onestep-postgres/src/onestep_postgres/connector.py`
- Modify: `plugins/onestep-postgres/src/onestep_postgres/resources.py`
- Modify: `plugins/onestep-postgres/src/onestep_postgres/__init__.py`
- Modify: `plugins/onestep-postgres/tests/test_postgres_plugin.py`

- [ ] **步骤 1：编写 Python factory 与 catalog 失败测试**

Extend `test_postgres_plugin.py` with assertions that:

```python
backend = connector.execution_backend(auto_create=True)
source = backend.source(
    namespace="agent-api",
    task_names=("run_agent",),
    worker_id="worker-1",
)
assert isinstance(backend, PostgresExecutionBackend)
assert isinstance(source, PostgresExecutionSource)
```

Catalog assertions:

```python
assert catalog["postgres_execution_source"].roles == ("source",)
assert catalog["postgres_execution_source"].connector_types == ("postgres",)
```

Add a strict YAML app that defines `postgres`, `postgres_execution_source`, and a `run_agent` task. Assert the task source is the built `PostgresExecutionSource` and the connector is shared by identity.

Add negative tests for unknown fields, empty `task_names`, heartbeat greater than one third of lease, non-positive numeric fields and a dependency that is not `PostgresConnector`.

- [ ] **步骤 2：运行插件测试并确认缺失 factory/resource**

Run:

```bash
uv run --all-packages pytest -q plugins/onestep-postgres/tests/test_postgres_plugin.py -k execution
```

Expected: failures report missing factory, exports and resource type.

- [ ] **步骤 3：添加 connector factory 与 backend source factory**

Add to `PostgresConnector`:

```python
def execution_backend(
    self,
    *,
    table: str = "onestep_executions",
    attempts_table: str = "onestep_execution_attempts",
    auto_create: bool = True,
    max_payload_bytes: int = 1024 * 1024,
    max_metadata_bytes: int = 64 * 1024,
    max_result_bytes: int = 1024 * 1024,
) -> PostgresExecutionBackend:
    return PostgresExecutionBackend(
        connector=self,
        table=table,
        attempts_table=attempts_table,
        auto_create=auto_create,
        max_payload_bytes=max_payload_bytes,
        max_metadata_bytes=max_metadata_bytes,
        max_result_bytes=max_result_bytes,
    )
```

Add `PostgresExecutionBackend.source()` with namespace/task/worker/batch/poll/lease/heartbeat parameters. Keep worker concerns out of core `ExecutionBackend` protocol.

- [ ] **步骤 4：注册 `postgres_execution_source`**

Allowed YAML fields must be exactly:

```python
frozenset(
    {
        "type",
        "connector",
        "namespace",
        "task_names",
        "table",
        "attempts_table",
        "batch_size",
        "poll_interval_s",
        "lease_duration_s",
        "heartbeat_interval_s",
        "worker_id",
        "auto_create",
        "max_payload_bytes",
        "max_metadata_bytes",
        "max_result_bytes",
    }
)
```

Catalog role is only `source`; do not change `CATALOG_ROLES`. The builder resolves the `postgres` connector, creates one backend, then calls `.source()`. Add a validation callback so strict mode fails before resource construction with field-qualified messages.

- [ ] **步骤 5：导出插件 API**

Export at least:

```python
PostgresExecutionBackend
PostgresExecutionSource
PostgresExecutionDelivery
ExecutionLease
HeartbeatResult
StaleExecutionLease
```

Do not export schema-internal SQLAlchemy table builders as stable user API.

- [ ] **步骤 6：运行完整的 PostgreSQL 非集成测试套件**

Run:

```bash
uv run --all-packages pytest -q -m "not integration" plugins/onestep-postgres/tests
```

Expected: all PostgreSQL plugin unit and runtime-contract tests pass.

- [ ] **步骤 7：提交插件接入**

```bash
git add plugins/onestep-postgres/src/onestep_postgres/connector.py plugins/onestep-postgres/src/onestep_postgres/resources.py plugins/onestep-postgres/src/onestep_postgres/__init__.py plugins/onestep-postgres/tests/test_postgres_plugin.py
git commit -m "feat(postgres): expose execution source"
```

## 任务 6：添加真实 PostgreSQL 并发门禁

**文件：**

- Modify: `docker-compose.integration.yml`
- Modify: `scripts/setup-integration-env.sh`
- Modify: `scripts/run-integration-tests.sh`
- Modify: `tests/test_database_plugin_integration.py`
- Create: `plugins/onestep-postgres/tests/integration/test_postgres_execution_live.py`
- Modify: `.github/workflows/plugin-postgres.yml`

- [ ] **步骤 1：编写集成测试设施的失败契约断言**

Extend `test_integration_harness_contains_database_services_and_tests()` with:

```python
assert "postgres:" in compose
assert "ONESTEP_POSTGRES_DSN" in setup
assert "plugins/onestep-postgres/tests/integration" in runner
```

Add assertions that `.github/workflows/plugin-postgres.yml` contains a PostgreSQL service, runs `-m integration`, and `publish-pypi` depends on both unit and live jobs.

- [ ] **步骤 2：运行测试设施契约并确认当前缺口**

Run:

```bash
uv run pytest -q tests/test_database_plugin_integration.py
```

Expected: the new PostgreSQL assertions fail; this proves the existing live tests are not in the unified gate.

- [ ] **步骤 3：将 PostgreSQL 加入本地集成测试设施**

Add a Compose service with explicit defaults:

```yaml
postgres:
  image: postgres:16-alpine
  container_name: onestep-postgres
  environment:
    POSTGRES_DB: onestep
    POSTGRES_USER: onestep
    POSTGRES_PASSWORD: onestep
  ports:
    - "5432:5432"
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U onestep -d onestep"]
    interval: 5s
    timeout: 5s
    retries: 30
```

In `setup-integration-env.sh`, add configurable host/port/database/user/password, wait through Python `psycopg.connect(connect_timeout=2)`, and output:

```bash
export ONESTEP_POSTGRES_DSN="postgresql+psycopg://onestep:onestep@127.0.0.1:5432/onestep"
```

Add `plugins/onestep-postgres/tests/integration` to the runner list adjacent to MySQL.

- [ ] **步骤 4：编写真实并发与 fencing 测试**

Create isolated table names with a UUID suffix and always drop them in `finally`. Required tests:

```text
test_concurrent_submit_with_same_idempotency_key_creates_one_execution_live
test_two_sources_claim_each_execution_once_live
test_heartbeat_prevents_takeover_live
test_expired_lease_takeover_fences_old_worker_live
test_cancel_and_complete_race_has_one_terminal_winner_live
test_retry_and_worker_restart_recover_live
```

Use two independent `PostgresConnector`/backend instances so the tests exercise separate connection pools. Coordinate races with threading or asyncio barriers; do not use sleeps as the only ordering mechanism. A small clock wait is allowed only to cross an actual database lease deadline after both sides confirm readiness.

- [ ] **步骤 5：在本地运行真实 PostgreSQL 测试**

Run:

```bash
./scripts/run-integration-tests.sh
```

Expected: all configured live suites pass, including `test_postgres_execution_live.py`; Compose services are removed automatically unless `KEEP_INTEGRATION_SERVICES=1`.

- [ ] **步骤 6：在 PostgreSQL 插件工作流中添加真实数据库 job**

Add `live-compatibility` using PostgreSQL 16 service credentials and:

```yaml
env:
  ONESTEP_POSTGRES_DSN: postgresql+psycopg://onestep:onestep@127.0.0.1:5432/onestep

steps:
  - uses: actions/checkout@v4
  - uses: actions/setup-python@v5
    with:
      python-version: "3.11"
  - uses: astral-sh/setup-uv@v5
  - run: uv sync --frozen --all-packages --extra test
  - run: uv run --all-packages python -m pytest -q -m integration plugins/onestep-postgres/tests/integration
```

Set `publish-pypi.needs` to `test`, `live-compatibility`, and `detect-version`; require both test jobs to succeed in the `if` expression.

- [ ] **步骤 7：重新运行测试设施契约**

Run:

```bash
uv run pytest -q tests/test_database_plugin_integration.py
```

Expected: all assertions pass.

- [ ] **步骤 8：提交真实数据库门禁**

```bash
git add docker-compose.integration.yml scripts/setup-integration-env.sh scripts/run-integration-tests.sh tests/test_database_plugin_integration.py plugins/onestep-postgres/tests/integration/test_postgres_execution_live.py .github/workflows/plugin-postgres.yml
git commit -m "test(postgres): gate execution lease semantics"
```

## 任务 7：记录 FastAPI 与 worker 用法

**文件：**

- Modify: `deploy/web-service-integration.md`
- Modify: `docs/broker/postgres.md`
- Modify: `plugins/onestep-postgres/README.md`
- Modify: `skills/onestep/references/python-api.md`
- Modify: `skills/onestep/references/connectors.md`

- [ ] **步骤 1：更新部署指南**

Add a “Tracked HTTP tasks with PostgreSQL” section to `deploy/web-service-integration.md` containing:

- separate FastAPI and worker process diagram;
- FastAPI lifespan that opens backend and closes connector;
- `POST /agent-runs`, `GET /agent-runs/{id}`, cancel and result examples;
- worker `backend.source()` wiring;
- explicit at-least-once, cancellation and idempotency warnings.

Keep the existing RabbitMQ example as the recommendation for untracked high-throughput messaging; do not rewrite unrelated deployment guidance.

- [ ] **步骤 2：更新 PostgreSQL 公共文档与插件 README**

Document both Python APIs:

```python
backend = pg.execution_backend(auto_create=True)
step = ExecutionClient(backend, namespace="agent-api")
execution = await step.submit("run_agent", payload, idempotency_key=request_id)
```

and:

```python
source = backend.source(
    namespace="agent-api",
    task_names=("run_agent",),
    worker_id="agent-worker-1",
)
```

Include the exact eight statuses, result behavior, lease configuration invariant, 1 MiB inline limits and production `auto_create=False` guidance.

- [ ] **步骤 3：更新 onestep skill 参考文档**

In `skills/onestep/references/python-api.md`, add the client methods and state that snapshots never auto-refresh. In `connectors.md`, add `postgres_execution_source` YAML and explain that FastAPI uses the Python backend while YAML wires the worker source.

- [ ] **步骤 4：构建文档**

Run:

```bash
pnpm --dir docs build
```

Expected: VitePress build succeeds without broken Markdown or Mermaid errors.

- [ ] **步骤 5：提交文档**

```bash
git add deploy/web-service-integration.md docs/broker/postgres.md plugins/onestep-postgres/README.md skills/onestep/references/python-api.md skills/onestep/references/connectors.md
git commit -m "docs: explain tracked postgres executions"
```

## 任务 8：准备兼容的核心包与插件发布

**文件：**

- Modify: `CHANGELOG.md`
- Modify: `pyproject.toml`
- Modify: `plugins/onestep-postgres/pyproject.toml`
- Modify: `uv.lock`

- [ ] **步骤 1：在 changelog 中记录兼容性影响**

Under `Unreleased`, add separate core `1.9.0` and `onestep-postgres 0.2.0` bullets covering:

- new execution client/models/protocol;
- optional managed runtime branch with ordinary Delivery compatibility;
- PostgreSQL task storage, lease, heartbeat, fencing and YAML source;
- additive TaskEvent metadata only;
- at-least-once and cooperative cancellation limitations.

- [ ] **步骤 2：升级包版本与最低依赖版本**

Apply exactly:

```toml
# root pyproject.toml
version = "1.9.0"
postgres = ["onestep-postgres>=0.2.0"]

# plugins/onestep-postgres/pyproject.toml
version = "0.2.0"
dependencies = [
    "onestep>=1.9.0",
    "SQLAlchemy>=2.0.0",
    "psycopg[binary]>=3.2.0",
]
```

Replace the PostgreSQL dependency floor in root `all`, `dev`, and `integration` extras as well.

- [ ] **步骤 3：刷新并验证 lockfile**

Run:

```bash
uv lock
uv lock --check
```

Expected: `uv.lock` records core `1.9.0`, plugin `0.2.0`, and no unrelated dependency upgrades beyond resolver-required metadata.

- [ ] **步骤 4：运行聚焦的核心包与插件测试套件**

Run:

```bash
uv run pytest -q tests/test_execution.py tests/contract/test_runtime_contract.py tests/test_packaging.py tests/test_database_plugin_integration.py
uv run --all-packages pytest -q -m "not integration" plugins/onestep-postgres/tests
```

Expected: all tests pass.

- [ ] **步骤 5：运行仓库可靠性门禁**

Run:

```bash
uv run pytest -q -m "not integration"
./scripts/run-reliability-checks.sh
```

Expected: core and every official plugin non-integration suite pass in isolated pytest processes.

- [ ] **步骤 6：构建发行包**

Run:

```bash
uv build --package onestep --out-dir dist/core --sdist --wheel --clear
uv build --package onestep-postgres --out-dir dist/postgres --sdist --wheel --clear
uvx twine check dist/core/* dist/postgres/*
```

Expected: both distributions build and pass metadata checks; importing the core wheel does not require SQLAlchemy.

- [ ] **步骤 7：检查最终 diff**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; changes are limited to execution core, PostgreSQL plugin, integration harness, docs, tests, changelog, versions and lockfile.

- [ ] **步骤 8：提交发布准备**

```bash
git add CHANGELOG.md pyproject.toml plugins/onestep-postgres/pyproject.toml uv.lock
git commit -m "chore: prepare execution backend releases"
```

## 任务 9：最终端到端验收

**文件：**

- Test all files changed by Tasks 1-8.

- [ ] **步骤 1：从干净服务状态运行真实 PostgreSQL 门禁**

Run:

```bash
./scripts/run-integration-tests.sh
```

Expected: PostgreSQL execution tests prove concurrent idempotency, non-duplicate claim, heartbeat protection, takeover fencing, cancellation race and retry recovery.

- [ ] **步骤 2：运行 FastAPI 形态的双进程冒烟脚本**

Create a temporary test fixture under pytest rather than committing an example server. The smoke must:

1. create one backend/client as the API side;
2. create a second connector/backend/source as the worker side;
3. submit `run_agent` and assert HTTP-facing snapshot is queued;
4. run one delivery through `DeliveryExecutor` with result `{"answer": 42}`;
5. query through the API-side client and assert `succeeded` plus result;
6. close both connectors.

Place the permanent regression in `plugins/onestep-postgres/tests/integration/test_postgres_execution_live.py` as `test_api_submit_worker_execute_and_query_live`.

- [ ] **步骤 3：重新运行新的端到端测试**

Run after loading the integration environment:

```bash
uv run --all-packages pytest -q -m integration plugins/onestep-postgres/tests/integration/test_postgres_execution_live.py::test_api_submit_worker_execute_and_query_live
```

Expected: one test passes and no task remains running or leased.

- [ ] **步骤 4：验证发布顺序**

The plugin workflow verifies that `onestep==1.9.0` exists before publishing `onestep-postgres==0.2.0`. Release in this order:

```text
1. merge implementation after all gates pass
2. publish/tag core v1.9.0
3. verify v1.9.0 is available from PyPI
4. publish onestep-postgres 0.2.0
5. install both from PyPI in a clean Python 3.9 environment and run import smoke
```

Do not bypass the plugin dependency check by loosening `onestep>=1.9.0`.

## 自检

- Spec coverage: FastAPI API、快照模型、八状态状态机、PostgreSQL schema、幂等、分页、租约、心跳、fencing、取消、重试、dead-letter、YAML、live integration、文档和发布均有对应任务。
- Compatibility: 没有修改 `Delivery` 抽象方法、`Envelope` 必填字段、TaskEvent kind、普通 ack 顺序或 catalog role。
- Scope: 没有 Gateway、DAG、强制终止、对象存储 result backend 或 broker/result-store 双写。
- Type consistency: 公共客户端统一为 `ExecutionClient`；快照为 `Execution`；插件类统一为 `PostgresExecutionBackend/Source/Delivery`；状态统一使用 `retrying`，没有 `retry_wait`。
- Test consistency: SQLite 只验证 CRUD 和确定性状态逻辑；`SKIP LOCKED`、并发领取和 fencing 必须由真实 PostgreSQL 测试证明。
- Release consistency: core `1.9.0` 先发布，plugin `0.2.0` 依赖 `onestep>=1.9.0` 后发布。
- 占位项扫描：本计划没有未决占位、未命名文件或未指定验证命令。

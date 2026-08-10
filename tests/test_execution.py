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
        self.open_count = 0
        self.close_count = 0

    async def open(self) -> None:
        self.open_count += 1

    async def close(self) -> None:
        self.close_count += 1

    async def submit(self, request):
        self.submissions.append(request)
        assert self.current is not None
        return self.current

    async def get(self, namespace, execution_id):
        self.queries.append((namespace, execution_id))
        return self.current

    async def list(self, query):
        self.queries.append(query)
        return ExecutionPage(
            items=(() if self.current is None else (self.current,)),
            next_cursor=None,
        )

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


def test_execution_client_manages_backend_lifecycle() -> None:
    async def scenario() -> None:
        backend = FakeBackend(snapshot(ExecutionStatus.QUEUED))
        client = ExecutionClient(backend, namespace="agent-api")

        async with client as managed:
            assert managed is client
            assert backend.open_count == 1
            assert backend.close_count == 0

        assert backend.close_count == 1

        with pytest.raises(RuntimeError, match="handler failed"):
            async with client:
                raise RuntimeError("handler failed")
        assert backend.open_count == 2
        assert backend.close_count == 2

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
        step = ExecutionClient(
            FakeBackend(snapshot(status, error=error)),
            namespace="agent-api",
        )
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
            FakeBackend(
                snapshot(
                    ExecutionStatus.SUCCEEDED,
                    execution_id=execution_id,
                    result=None,
                )
            ),
            namespace="agent-api",
        )
        assert await succeeded.result(execution_id) is None

    asyncio.run(scenario())


def test_validation_and_boundary_copies() -> None:
    async def scenario() -> None:
        queued = snapshot(ExecutionStatus.QUEUED)
        backend = FakeBackend(queued)
        step = ExecutionClient(backend, namespace=" agent-api ")
        payload = {"nested": ["before"]}
        metadata = {"owner": "u-1"}
        await step.submit(" run_agent ", payload, metadata=metadata)
        payload["nested"].append("after")
        metadata["owner"] = "u-2"
        assert backend.submissions[0].payload == {"nested": ["before"]}
        assert backend.submissions[0].metadata == {"owner": "u-1"}

        with pytest.raises(ValueError):
            ExecutionClient(backend, namespace=" ")
        with pytest.raises(ValueError):
            await step.submit(" ", {})
        with pytest.raises(ValueError):
            await step.submit("run_agent", {}, idempotency_key=" ")
        with pytest.raises(ValueError):
            await step.submit("run_agent", {}, delay_s=-1)
        with pytest.raises(ValueError):
            await step.submit("run_agent", {}, expires_at=datetime(2026, 8, 9))
        with pytest.raises(ValueError):
            await step.list(limit=0)
        with pytest.raises(ValueError):
            await step.list(limit=201)
        with pytest.raises(ValueError):
            await step.get("not-a-uuid")
        with pytest.raises(TypeError, match="reason must be a string or None"):
            await step.cancel(uuid4(), reason=123)
        with pytest.raises(ValueError):
            await step.cancel(uuid4(), reason="x" * 501)

    asyncio.run(scenario())


def test_execution_error_normalizes_text_fields() -> None:
    error = ExecutionError(
        kind="  error  ",
        exception_type="  ValueError  ",
        stage="  handler  ",
        backend="  postgres  ",
        operation="  heartbeat  ",
        connector_kind="  transient  ",
    )

    assert error == ExecutionError(
        kind="error",
        exception_type="ValueError",
        stage="handler",
        backend="postgres",
        operation="heartbeat",
        connector_kind="transient",
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"kind": " "},
        {"kind": "x" * 65},
        {"kind": 1},
        {"exception_type": " "},
        {"exception_type": "x" * 256},
        {"exception_type": object()},
        {"stage": " "},
        {"backend": "x" * 256},
        {"operation": object()},
        {"connector_kind": " "},
    ],
)
def test_execution_error_rejects_invalid_text_fields(overrides) -> None:
    values = {"kind": "error", "exception_type": "ValueError"}
    values.update(overrides)

    with pytest.raises((TypeError, ValueError)):
        ExecutionError(**values)


def test_cancel_normalizes_reason_and_forwards_list() -> None:
    async def scenario() -> None:
        execution_id = uuid4()
        backend = FakeBackend(snapshot(ExecutionStatus.QUEUED, execution_id=execution_id))
        step = ExecutionClient(backend, namespace="agent-api")
        await step.cancel(execution_id, reason="  no longer needed  ")
        await step.cancel(execution_id, reason="  ")
        await step.list(task_name="run_agent", status=ExecutionStatus.QUEUED, limit=2)
        assert backend.cancel_requests == [
            ("agent-api", execution_id, "no longer needed"),
            ("agent-api", execution_id, None),
        ]
        query = backend.queries[-1]
        assert query.namespace == "agent-api"
        assert query.task_name == "run_agent"
        assert query.status is ExecutionStatus.QUEUED
        assert query.limit == 2

    asyncio.run(scenario())

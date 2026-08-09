from __future__ import annotations

import asyncio
import base64
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from onestep.execution import (
    ExecutionCompletion,
    ExecutionConflict,
    ExecutionEncodingError,
    ExecutionQuery,
    ExecutionRequest,
    ExecutionStatus,
)
from onestep_postgres import PostgresConnector
from onestep_postgres.execution_backend import PostgresExecutionBackend


NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


def _backend(path: Path, *, auto_create: bool = True, clock=None) -> PostgresExecutionBackend:
    connector = PostgresConnector(f"sqlite:///{path}")
    return PostgresExecutionBackend(
        connector=connector,
        auto_create=auto_create,
        clock=clock or (lambda: NOW),
    )


def _request(**overrides):
    values = {
        "namespace": "agent-api",
        "task_name": "run_agent",
        "payload": {"prompt": "hello"},
        "metadata": {"requested_by": "u-1"},
    }
    values.update(overrides)
    return ExecutionRequest(**values)


def test_submit_get_and_successful_none_result_round_trip(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        submitted = await backend.submit(_request())
        assert submitted.status is ExecutionStatus.QUEUED
        assert submitted.result is None
        loaded = await backend.get("agent-api", submitted.id)
        assert loaded == submitted

    asyncio.run(scenario())


def test_submit_rejects_payload_over_one_mib(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        with pytest.raises(ExecutionEncodingError, match="configured limit"):
            await backend.submit(_request(payload={"value": "x" * (1024 * 1024)}))

    asyncio.run(scenario())


def test_submit_tagged_codec_round_trips_uuid_datetime_decimal_and_bytes(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        payload = {
            "id": uuid4(),
            "at": NOW,
            "amount": Decimal("12.30"),
            "data": b"bytes",
        }
        submitted = await backend.submit(_request(payload=payload))
        loaded = await backend.get("agent-api", submitted.id)
        assert loaded is not None
        assert loaded.payload == payload

    asyncio.run(scenario())


def test_same_idempotency_key_and_digest_returns_original_execution(tmp_path: Path) -> None:
    async def scenario() -> None:
        path = tmp_path / "execution.db"
        first_backend = _backend(path)
        second_backend = _backend(path)
        first = await first_backend.submit(_request(idempotency_key="request-1"))
        second = await second_backend.submit(_request(idempotency_key="request-1"))
        assert second.id == first.id

    asyncio.run(scenario())


def test_same_idempotency_key_with_different_payload_raises_conflict(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        await backend.submit(_request(idempotency_key="request-1"))
        with pytest.raises(ExecutionConflict):
            await backend.submit(
                _request(idempotency_key="request-1", payload={"prompt": "different"})
            )

    asyncio.run(scenario())


def test_cancel_queued_and_retrying_becomes_cancelled(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        queued = await backend.submit(_request())
        cancelled = await backend.request_cancel(
            "agent-api", queued.id, reason="stop"
        )
        assert cancelled is not None
        assert cancelled.status is ExecutionStatus.CANCELLED

        retrying = await backend.submit(_request(task_name="retry"))
        with backend.engine.begin() as conn:
            conn.execute(
                sa.update(backend.tables.executions)
                .where(backend.tables.executions.c.id == retrying.id)
                .values(status=ExecutionStatus.RETRYING.value)
            )
        cancelled_retry = await backend.request_cancel(
            "agent-api", retrying.id, reason=None
        )
        assert cancelled_retry is not None
        assert cancelled_retry.status is ExecutionStatus.CANCELLED

    asyncio.run(scenario())


def test_cancel_running_becomes_cancel_requested(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        execution = await backend.submit(_request())
        with backend.engine.begin() as conn:
            conn.execute(
                sa.update(backend.tables.executions)
                .where(backend.tables.executions.c.id == execution.id)
                .values(status=ExecutionStatus.RUNNING.value)
            )
        cancelled = await backend.request_cancel(
            "agent-api", execution.id, reason="stop"
        )
        assert cancelled is not None
        assert cancelled.status is ExecutionStatus.CANCEL_REQUESTED

    asyncio.run(scenario())


def test_cancel_terminal_is_idempotent(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        execution = await backend.submit(_request())
        with backend.engine.begin() as conn:
            conn.execute(
                sa.update(backend.tables.executions)
                .where(backend.tables.executions.c.id == execution.id)
                .values(status=ExecutionStatus.SUCCEEDED.value, result={"ok": True})
            )
        first = await backend.request_cancel("agent-api", execution.id, reason="stop")
        second = await backend.request_cancel("agent-api", execution.id, reason="again")
        assert first == second
        assert first is not None and first.status is ExecutionStatus.SUCCEEDED

    asyncio.run(scenario())


def test_list_filters_task_and_status_with_keyset_cursor(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        for index in range(4):
            await backend.submit(
                _request(
                    task_name="run_agent" if index < 3 else "other",
                    payload={"index": index},
                )
            )
        first = await backend.list(
            ExecutionQuery(namespace="agent-api", task_name="run_agent", limit=2)
        )
        second = await backend.list(
            ExecutionQuery(
                namespace="agent-api",
                task_name="run_agent",
                limit=2,
                cursor=first.next_cursor,
            )
        )
        ids = [item.id for item in (*first.items, *second.items)]
        assert len(ids) == 3
        assert len(set(ids)) == 3
        assert second.next_cursor is None

        with backend.engine.begin() as conn:
            conn.execute(
                sa.update(backend.tables.executions)
                .where(backend.tables.executions.c.id == ids[0])
                .values(status=ExecutionStatus.SUCCEEDED.value)
            )
        filtered = await backend.list(
            ExecutionQuery(
                namespace="agent-api",
                status=ExecutionStatus.SUCCEEDED,
                limit=10,
            )
        )
        assert [item.id for item in filtered.items] == [ids[0]]

    asyncio.run(scenario())


def test_list_rejects_cursor_with_unknown_version(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        await backend.submit(_request())
        value = base64.urlsafe_b64encode(
            json.dumps({"v": 2, "created_at": NOW.isoformat(), "id": str(uuid4())}).encode()
        ).decode()
        with pytest.raises(ValueError, match="unknown cursor version"):
            await backend.list(
                ExecutionQuery(namespace="agent-api", cursor=value)
            )

    asyncio.run(scenario())


def test_open_without_auto_create_reports_missing_tables(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "missing.db", auto_create=False)
        with pytest.raises(RuntimeError, match="missing execution tables"):
            await backend.open()

    asyncio.run(scenario())

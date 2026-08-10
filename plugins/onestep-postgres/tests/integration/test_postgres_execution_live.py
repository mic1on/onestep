from __future__ import annotations

import asyncio
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import sqlalchemy as sa

from onestep import (
    ExecutionClient,
    ExecutionCompletion,
    ExecutionRequest,
    ExecutionStatus,
    OneStepApp,
)
from onestep.runtime import TaskRunner
from onestep_postgres import (
    PostgresConnector,
    PostgresExecutionBackend,
    StaleExecutionLease,
)


pytestmark = pytest.mark.integration


if not os.getenv("ONESTEP_POSTGRES_DSN"):
    pytest.skip("set ONESTEP_POSTGRES_DSN to run PostgreSQL integration tests", allow_module_level=True)


def _dsn() -> str:
    return os.environ["ONESTEP_POSTGRES_DSN"]


def _names(prefix: str) -> tuple[str, str]:
    suffix = uuid.uuid4().hex[:12]
    return f"{prefix}_executions_{suffix}", f"{prefix}_attempts_{suffix}"


async def _close_and_drop(connectors: list[PostgresConnector], tables: tuple[str, str]) -> None:
    engine = sa.create_engine(_dsn(), future=True)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text(f'DROP TABLE IF EXISTS "{tables[1]}"'))
            conn.execute(sa.text(f'DROP TABLE IF EXISTS "{tables[0]}"'))
    finally:
        engine.dispose()
    await asyncio.gather(*(connector.close() for connector in connectors))


class _PauseAfterSelect:
    def __init__(
        self,
        conn,
        *,
        selected: threading.Event,
        resume: threading.Event,
    ) -> None:
        self._conn = conn
        self._selected = selected
        self._resume = resume
        self._paused = False

    def execute(self, statement, *args, **kwargs):
        result = self._conn.execute(statement, *args, **kwargs)
        if not self._paused and isinstance(statement, sa.sql.Select):
            self._paused = True
            self._selected.set()
            if not self._resume.wait(timeout=10):
                raise TimeoutError("expired lease cleanup barrier timed out")
        return result


def test_concurrent_auto_create_is_serialized_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("autocreate")
        connectors = [PostgresConnector(_dsn()) for _ in range(6)]
        backends = [
            connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            for connector in connectors
        ]
        try:
            await asyncio.gather(*(backend.open() for backend in backends))
        finally:
            await _close_and_drop(connectors, (execution_table, attempts_table))

    asyncio.run(scenario())


def test_concurrent_submit_with_same_idempotency_key_creates_one_execution_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("idem")
        first_connector = PostgresConnector(_dsn())
        second_connector = PostgresConnector(_dsn())
        try:
            first = first_connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            second = second_connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            await first.open()
            await second.open()
            client_a = ExecutionClient(first, namespace="agent-api")
            client_b = ExecutionClient(second, namespace="agent-api")
            results = await asyncio.gather(
                client_a.submit("run_agent", {"prompt": "hello"}, idempotency_key="request-1"),
                client_b.submit("run_agent", {"prompt": "hello"}, idempotency_key="request-1"),
            )
            assert results[0].id == results[1].id
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_expired_lease_cleanup_does_not_overwrite_renewal_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("cleanupcas")
        first_connector = PostgresConnector(_dsn())
        second_connector = PostgresConnector(_dsn())
        base_time = datetime(2026, 8, 9, tzinfo=timezone.utc)
        cleanup_clock = {"now": base_time}
        heartbeat_clock = {"now": base_time + timedelta(seconds=0.5)}
        selected = threading.Event()
        resume = threading.Event()
        first = PostgresExecutionBackend(
            connector=first_connector,
            table=execution_table,
            attempts_table=attempts_table,
            clock=lambda: cleanup_clock["now"],
        )
        second = PostgresExecutionBackend(
            connector=second_connector,
            table=execution_table,
            attempts_table=attempts_table,
            clock=lambda: heartbeat_clock["now"],
        )
        try:
            await first.open()
            await second.open()
            await first.submit(
                ExecutionRequest(
                    namespace="agent-api",
                    task_name="run_agent",
                    payload={"value": 1},
                )
            )
            [lease] = await first.claim(
                "agent-api",
                ("run_agent",),
                1,
                1,
                "worker-a",
            )
            cleanup_clock["now"] = base_time + timedelta(seconds=2)

            def cleanup() -> None:
                with first.engine.begin() as raw_conn:
                    conn = _PauseAfterSelect(
                        raw_conn,
                        selected=selected,
                        resume=resume,
                    )
                    first._release_expired_leases_sync(
                        conn,
                        first.tables.attempts,
                        cleanup_clock["now"],
                    )

            cleanup_task = asyncio.create_task(asyncio.to_thread(cleanup))
            assert await asyncio.to_thread(selected.wait, 10)
            heartbeat = await second.heartbeat(
                lease.execution.id,
                lease.attempt_id,
                lease.lease_token,
                30,
            )
            resume.set()
            await cleanup_task

            current = await first.get("agent-api", lease.execution.id)
            assert current is not None and current.status is ExecutionStatus.RUNNING
            with first.engine.begin() as conn:
                attempt_status = conn.execute(
                    sa.select(first.tables.attempts.c.status).where(
                        first.tables.attempts.c.id == lease.attempt_id
                    )
                ).scalar_one()
            assert attempt_status == "running"
            assert heartbeat.lease_expires_at > cleanup_clock["now"]
        finally:
            resume.set()
            await _close_and_drop(
                [first_connector, second_connector],
                (execution_table, attempts_table),
            )

    asyncio.run(scenario())


def test_two_sources_claim_each_execution_once_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("claim")
        first_connector = PostgresConnector(_dsn())
        second_connector = PostgresConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            second = second_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await first.open()
            await second.open()
            for index in range(6):
                await first.submit(
                    ExecutionRequest(
                        namespace="agent-api",
                        task_name="run_agent",
                        payload={"index": index},
                    )
                )
            claimed = await asyncio.gather(
                first.claim("agent-api", ("run_agent",), 3, 30, "worker-a"),
                second.claim("agent-api", ("run_agent",), 3, 30, "worker-b"),
            )
            ids = [lease.execution.id for batch in claimed for lease in batch]
            assert len(ids) == 6
            assert len(set(ids)) == 6
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_heartbeat_prevents_takeover_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("heartbeat")
        first_connector = PostgresConnector(_dsn())
        second_connector = PostgresConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            second = second_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await first.open()
            await second.open()
            await first.submit(
                ExecutionRequest(
                    namespace="agent-api", task_name="run_agent", payload={"value": 1}
                )
            )
            [lease] = await first.claim("agent-api", ("run_agent",), 1, 5, "worker-a")
            await first.heartbeat(lease.execution.id, lease.attempt_id, lease.lease_token, 5)
            assert await second.claim("agent-api", ("run_agent",), 1, 5, "worker-b") == ()
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_expired_lease_takeover_fences_old_worker_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("fence")
        first_connector = PostgresConnector(_dsn())
        second_connector = PostgresConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            second = second_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await first.open()
            await second.open()
            await first.submit(
                ExecutionRequest(
                    namespace="agent-api", task_name="run_agent", payload={"value": 1}
                )
            )
            [old] = await first.claim("agent-api", ("run_agent",), 1, 0.2, "worker-a")
            await asyncio.sleep(0.35)
            [new] = await second.claim("agent-api", ("run_agent",), 1, 5, "worker-b")
            assert old.lease_token != new.lease_token
            with pytest.raises(StaleExecutionLease):
                await first.complete(
                    old.execution.id,
                    old.attempt_id,
                    old.lease_token,
                    ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"old": True}),
                )
            await second.complete(
                new.execution.id,
                new.attempt_id,
                new.lease_token,
                ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"new": True}),
            )
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_cancel_and_complete_race_has_one_terminal_winner_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("cancelrace")
        first_connector = PostgresConnector(_dsn())
        second_connector = PostgresConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            second = second_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await first.open()
            await second.open()
            submitted = await first.submit(
                ExecutionRequest(
                    namespace="agent-api", task_name="run_agent", payload={"value": 1}
                )
            )
            [lease] = await first.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
            start = asyncio.Event()

            async def cancel():
                await start.wait()
                return await second.request_cancel("agent-api", submitted.id, reason="stop")

            async def complete():
                await start.wait()
                try:
                    return await first.complete(
                        lease.execution.id,
                        lease.attempt_id,
                        lease.lease_token,
                        ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"ok": True}),
                    )
                except StaleExecutionLease:
                    return None

            cancel_task = asyncio.create_task(cancel())
            complete_task = asyncio.create_task(complete())
            start.set()
            cancelled, completed = await asyncio.gather(cancel_task, complete_task)
            final = await first.get("agent-api", submitted.id)
            assert final is not None and final.terminal
            assert final.status in {ExecutionStatus.SUCCEEDED, ExecutionStatus.CANCELLED}
            assert completed is None or completed.status is final.status
            if final.status is ExecutionStatus.CANCELLED:
                assert final.result is None
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_retry_and_worker_restart_recover_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("restart")
        first_connector = PostgresConnector(_dsn())
        second_connector = PostgresConnector(_dsn())
        try:
            first = first_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            second = second_connector.execution_backend(table=execution_table, attempts_table=attempts_table)
            await first.open()
            await second.open()
            await first.submit(
                ExecutionRequest(
                    namespace="agent-api", task_name="run_agent", payload={"value": 1}
                )
            )
            [old] = await first.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
            await first.complete(
                old.execution.id,
                old.attempt_id,
                old.lease_token,
                ExecutionCompletion(status=ExecutionStatus.RETRYING, delay_s=0),
            )
            [restarted] = await second.claim("agent-api", ("run_agent",), 1, 30, "worker-b")
            assert restarted.execution.attempts == 2
        finally:
            await _close_and_drop([first_connector, second_connector], (execution_table, attempts_table))

    asyncio.run(scenario())


def test_api_submit_worker_execute_and_query_live():
    async def scenario() -> None:
        execution_table, attempts_table = _names("e2e")
        api_connector = PostgresConnector(_dsn())
        worker_connector = PostgresConnector(_dsn())
        try:
            api_backend = api_connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
            )
            worker_backend = worker_connector.execution_backend(
                table=execution_table,
                attempts_table=attempts_table,
                auto_create=False,
            )
            await api_backend.open()
            client = ExecutionClient(api_backend, namespace="agent-api")
            source = worker_backend.source(
                namespace="agent-api",
                task_names=("run_agent",),
                worker_id="agent-worker-1",
            )
            await source.open()
            submitted = await client.submit("run_agent", {"prompt": "hello"}, idempotency_key="e2e-1")
            assert submitted.status is ExecutionStatus.QUEUED
            [delivery] = await source.fetch(1)
            app = OneStepApp("live-worker")

            @app.task(source=source)
            async def run_agent(ctx, payload):
                return {"answer": 42}

            await TaskRunner(app, app.tasks[0])._handle_delivery(delivery)
            result = await client.result(submitted.id)
            assert result == {"answer": 42}
            final = await client.get(submitted.id)
            assert final is not None and final.status is ExecutionStatus.SUCCEEDED
        finally:
            await _close_and_drop([api_connector, worker_connector], (execution_table, attempts_table))

    asyncio.run(scenario())

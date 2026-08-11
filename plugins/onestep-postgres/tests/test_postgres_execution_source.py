from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from onestep import OneStepApp
from onestep.envelope import Envelope
from onestep.execution import (
    Execution,
    ExecutionCompletion,
    ExecutionErrorDetail,
    ExecutionLeaseLost,
    ExecutionQuery,
    ExecutionStatus,
)
from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)
from onestep.runtime import TaskRunner
from onestep_postgres import PostgresConnector
from onestep_postgres.execution_backend import (
    ExecutionLease,
    HeartbeatResult,
    PostgresExecutionBackend,
    StaleExecutionLease,
)
from onestep_postgres.execution_source import (
    PostgresExecutionDelivery,
    PostgresExecutionSource,
)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, **kwargs) -> None:
        self.current += timedelta(**kwargs)


def test_stale_execution_lease_uses_core_lease_loss_contract() -> None:
    assert issubclass(StaleExecutionLease, ExecutionLeaseLost)


def _backend(
    path: Path,
    clock: MutableClock,
    *,
    reclaim_batch_size: int = 100,
) -> PostgresExecutionBackend:
    connector = PostgresConnector(f"sqlite:///{path}")
    return PostgresExecutionBackend(
        connector=connector,
        clock=clock,
        reclaim_batch_size=reclaim_batch_size,
    )


def _request(task_name="run_agent", **kwargs):
    from onestep.execution import ExecutionRequest

    return ExecutionRequest(
        namespace="agent-api",
        task_name=task_name,
        payload=kwargs.pop("payload", {"prompt": "hello"}),
        metadata=kwargs.pop("metadata", {"requested_by": "u-1"}),
        **kwargs,
    )


def test_source_rejects_multiple_task_names() -> None:
    with pytest.raises(ValueError, match="exactly one task name"):
        PostgresExecutionSource(
            backend=object(),
            namespace="agent-api",
            task_names=("task_a", "task_b"),
            worker_id="worker-1",
        )


def test_source_rejects_task_name_mismatch() -> None:
    source = PostgresExecutionSource(
        backend=object(),
        namespace="agent-api",
        task_names=("task_a",),
        worker_id="worker-1",
    )

    with pytest.raises(ValueError, match="configured for task 'task_a'"):
        source.validate_task("task_b")


def test_source_requires_exactly_one_dsn_or_backend() -> None:
    source_options = {
        "namespace": "agent-api",
        "task_names": ("run_agent",),
        "worker_id": "worker-1",
    }

    with pytest.raises(ValueError, match="exactly one of dsn or backend"):
        PostgresExecutionSource(**source_options)
    with pytest.raises(ValueError, match="exactly one of dsn or backend"):
        PostgresExecutionSource(
            dsn="sqlite:///unused.db",
            backend=object(),
            **source_options,
        )


def test_direct_dsn_source_lazily_owns_backend_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = PostgresExecutionSource(
            dsn=f"sqlite:///{tmp_path / 'direct-source.db'}",
            table="executions",
            attempts_table="attempts",
            auto_create=True,
            reclaim_batch_size=7,
            namespace="agent-api",
            task_names=("run_agent",),
            worker_id="worker-1",
        )

        assert source.backend.connector is None
        assert source.backend.table_name == "executions"
        assert source.backend.attempts_table_name == "attempts"
        assert source.backend.reclaim_batch_size == 7

        await source.open()
        first_connector = source.backend.connector
        assert first_connector is not None

        await source.close()
        assert source.backend.connector is None

        await source.open()
        assert source.backend.connector is not first_connector
        await source.close()

    asyncio.run(scenario())


def test_direct_dsn_source_rebuilds_backend_after_process_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = PostgresExecutionSource(
            dsn=f"sqlite:///{tmp_path / 'direct-source-fork.db'}",
            namespace="agent-api",
            task_names=("run_agent",),
            worker_id="worker-1",
        )
        await source.open()
        first_connector = source.backend.connector
        first_engine = source.backend.engine
        assert first_connector is not None

        source.backend._pid -= 1
        await source.open()

        assert source.backend.connector is not first_connector
        assert source.backend.engine is not first_engine
        await source.close()

    asyncio.run(scenario())


def test_claim_skips_delayed_execution_until_available_at(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        await backend.submit(_request(delay_s=10))
        assert await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-1") == ()
        clock.advance(seconds=10)
        assert len(await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-1")) == 1

    asyncio.run(scenario())


def test_claim_sets_running_attempt_and_envelope_attempts_zero(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        submitted = await backend.submit(_request())
        [lease] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-1")
        assert lease.execution.status is ExecutionStatus.RUNNING
        assert lease.execution.attempts == 1
        assert lease.execution.attempts - 1 == 0
        assert lease.attempt_id != submitted.id

    asyncio.run(scenario())


def test_claim_marks_expired_queued_execution_terminal(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        await backend.submit(_request(expires_at=clock.current - timedelta(seconds=1)))
        assert await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-1") == ()
        page = await backend.list(ExecutionQuery(namespace="agent-api"))
        assert page.items[0].status is ExecutionStatus.EXPIRED

    asyncio.run(scenario())


def test_claim_marks_expired_retrying_execution_terminal(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        [lease] = await _claim_one(backend)
        with backend.engine.begin() as conn:
            conn.execute(
                sa.update(backend.tables.executions)
                .where(backend.tables.executions.c.id == lease.execution.id)
                .values(expires_at=clock.current + timedelta(seconds=5))
            )
        completed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            ExecutionCompletion(status=ExecutionStatus.RETRYING, delay_s=0),
        )
        assert completed.status is ExecutionStatus.RETRYING

        clock.advance(seconds=5)
        assert await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-2") == ()
        page = await backend.list(ExecutionQuery(namespace="agent-api"))
        assert page.items[0].status is ExecutionStatus.EXPIRED

    asyncio.run(scenario())


def test_heartbeat_extends_only_matching_lease(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        [lease] = await _claim_one(backend)
        clock.advance(seconds=5)
        heartbeat = await backend.heartbeat(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            30,
        )
        assert heartbeat.lease_expires_at == clock.current + timedelta(seconds=30)
        with pytest.raises(StaleExecutionLease):
            await backend.heartbeat(
                lease.execution.id,
                lease.attempt_id,
                uuid4(),
                30,
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ("heartbeat", "complete", "release"))
def test_expired_lease_rejects_worker_writes(tmp_path: Path, operation: str) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / f"expired-{operation}.db", clock)
        [lease] = await _claim_one(backend)
        clock.advance(seconds=30)

        with pytest.raises(StaleExecutionLease):
            if operation == "heartbeat":
                await backend.heartbeat(
                    lease.execution.id,
                    lease.attempt_id,
                    lease.lease_token,
                    30,
                )
            elif operation == "complete":
                await backend.complete(
                    lease.execution.id,
                    lease.attempt_id,
                    lease.lease_token,
                    ExecutionCompletion(
                        status=ExecutionStatus.SUCCEEDED,
                        result={"late": True},
                    ),
                )
            else:
                await backend.release(
                    lease.execution.id,
                    lease.attempt_id,
                    lease.lease_token,
                )

        current = await backend.get("agent-api", lease.execution.id)
        assert current is not None and current.status is ExecutionStatus.RUNNING

    asyncio.run(scenario())


async def _claim_one(backend: PostgresExecutionBackend) -> tuple[ExecutionLease, ...]:
    await backend.submit(_request())
    return await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-1")


def test_expired_running_lease_creates_new_attempt_and_fences_old_token(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        [first] = await _claim_one(backend)
        clock.advance(seconds=30)
        [second] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-2")
        assert second.lease_token != first.lease_token
        assert second.execution.attempts == 2
        with pytest.raises(StaleExecutionLease):
            await backend.complete(
                first.execution.id,
                first.attempt_id,
                first.lease_token,
                ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"stale": True}),
            )

    asyncio.run(scenario())


def test_expired_running_lease_reclaims_even_after_business_expiry(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        submitted = await backend.submit(
            _request(expires_at=clock.current + timedelta(seconds=5))
        )
        [first] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-1")
        assert first.execution.id == submitted.id
        clock.advance(seconds=30)

        [second] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-2")

        assert second.execution.status is ExecutionStatus.RUNNING
        assert second.execution.attempts == 2
        assert second.lease_token != first.lease_token
        with backend.engine.begin() as conn:
            old_attempt_status = conn.execute(
                sa.select(backend.tables.attempts.c.status).where(
                    backend.tables.attempts.c.id == first.attempt_id
                )
            ).scalar_one()
        assert old_attempt_status == "lease_lost"
        with pytest.raises(StaleExecutionLease):
            await backend.complete(
                first.execution.id,
                first.attempt_id,
                first.lease_token,
                ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"stale": True}),
            )

    asyncio.run(scenario())


def test_claim_reclaims_stale_leases_in_bounded_batches(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(
            tmp_path / "bounded-reclaim.db",
            clock,
            reclaim_batch_size=1,
        )
        await backend.submit(_request(payload={"index": 1}))
        await backend.submit(_request(payload={"index": 2}))
        assert len(
            await backend.claim(
                "agent-api",
                ("run_agent",),
                2,
                30,
                "worker-1",
            )
        ) == 2
        clock.advance(seconds=30)

        assert len(
            await backend.claim(
                "agent-api",
                ("run_agent",),
                1,
                30,
                "worker-2",
            )
        ) == 1
        first_pass = await backend.list(ExecutionQuery(namespace="agent-api"))
        assert sorted(item.attempts for item in first_pass.items) == [1, 2]

        assert len(
            await backend.claim(
                "agent-api",
                ("run_agent",),
                1,
                30,
                "worker-2",
            )
        ) == 1
        second_pass = await backend.list(ExecutionQuery(namespace="agent-api"))
        assert sorted(item.attempts for item in second_pass.items) == [2, 2]

    asyncio.run(scenario())


def test_expired_cancel_requested_lease_becomes_cancelled(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        [lease] = await _claim_one(backend)
        cancelled = await backend.request_cancel("agent-api", lease.execution.id, reason="stop")
        assert cancelled is not None and cancelled.status is ExecutionStatus.CANCEL_REQUESTED
        clock.advance(seconds=30)
        assert await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-2") == ()
        current = await backend.get("agent-api", lease.execution.id)
        assert current is not None and current.status is ExecutionStatus.CANCELLED

    asyncio.run(scenario())


def test_complete_success_updates_execution_and_attempt_in_one_transaction(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        [lease] = await _claim_one(backend)
        completed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"answer": 42}),
        )
        assert completed.status is ExecutionStatus.SUCCEEDED
        assert completed.result == {"answer": 42}
        with backend.engine.begin() as conn:
            attempt = conn.execute(
                sa.select(backend.tables.attempts).where(
                    backend.tables.attempts.c.id == lease.attempt_id
                )
            ).mappings().one()
        assert attempt["status"] == "succeeded"

    asyncio.run(scenario())


def test_cancel_request_wins_over_late_success_completion(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "cancel-success-race.db", clock)
        [lease] = await _claim_one(backend)
        requested = await backend.request_cancel(
            "agent-api", lease.execution.id, reason="stop"
        )
        assert requested is not None
        assert requested.status is ExecutionStatus.CANCEL_REQUESTED

        completed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            ExecutionCompletion(
                status=ExecutionStatus.SUCCEEDED,
                result=object(),
            ),
        )

        assert completed.status is ExecutionStatus.CANCELLED
        assert completed.result is None
        assert completed.error is None
        current = await backend.get("agent-api", lease.execution.id)
        assert current == completed
        with backend.engine.begin() as conn:
            attempt = conn.execute(
                sa.select(backend.tables.attempts).where(
                    backend.tables.attempts.c.id == lease.attempt_id
                )
            ).mappings().one()
        assert attempt["status"] == "cancelled"
        assert attempt["error"] is None
        assert "result" not in attempt

    asyncio.run(scenario())


def test_runner_converges_cancel_and_success_race_to_cancelled(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "cancel-success-runner.db", clock)
        submitted = await backend.submit(_request())
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            worker_id="worker-1",
        )
        [delivery] = await source.fetch(1)
        app = OneStepApp("cancel-success-race")

        @app.task(name="run_agent", source=source)
        async def run_agent(ctx, payload):
            requested = await backend.request_cancel(
                "agent-api", submitted.id, reason="stop"
            )
            assert requested is not None
            assert requested.status is ExecutionStatus.CANCEL_REQUESTED
            return {"must_not_persist": True}

        with pytest.raises(asyncio.CancelledError):
            await TaskRunner(app, app.tasks[0])._handle_delivery(delivery)

        current = await backend.get("agent-api", submitted.id)
        assert current is not None
        assert current.status is ExecutionStatus.CANCELLED
        assert current.result is None

    asyncio.run(scenario())


def test_terminal_success_replay_requires_identical_result(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "success-replay.db", clock)
        [lease] = await _claim_one(backend)
        completion = ExecutionCompletion(
            status=ExecutionStatus.SUCCEEDED,
            result={"answer": {"b": 2, "a": 1}},
        )
        replay_completion = ExecutionCompletion(
            status=ExecutionStatus.SUCCEEDED,
            result={"answer": {"a": 1, "b": 2}},
        )

        completed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            completion,
        )
        replayed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            replay_completion,
        )
        assert replayed == completed

        with pytest.raises(StaleExecutionLease):
            await backend.complete(
                lease.execution.id,
                lease.attempt_id,
                lease.lease_token,
                ExecutionCompletion(
                    status=ExecutionStatus.SUCCEEDED,
                    result={"answer": {"a": 1, "b": 3}},
                ),
            )

    asyncio.run(scenario())


def test_terminal_failure_replay_requires_identical_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "failure-replay.db", clock)
        [lease] = await _claim_one(backend)
        first_error = ExecutionErrorDetail(kind="error", exception_type="FirstError")
        completion = ExecutionCompletion(
            status=ExecutionStatus.FAILED,
            error=first_error,
        )

        completed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            completion,
        )
        replayed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            completion,
        )
        assert replayed == completed

        with pytest.raises(StaleExecutionLease):
            await backend.complete(
                lease.execution.id,
                lease.attempt_id,
                lease.lease_token,
                ExecutionCompletion(
                    status=ExecutionStatus.FAILED,
                    error=ExecutionErrorDetail(
                        kind="error",
                        exception_type="SecondError",
                    ),
                ),
            )

    asyncio.run(scenario())


def test_complete_retry_sets_available_at_and_clears_lease(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        [lease] = await _claim_one(backend)
        completed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            ExecutionCompletion(status=ExecutionStatus.RETRYING, delay_s=5),
        )
        assert completed.status is ExecutionStatus.RETRYING
        assert completed.available_at == clock.current + timedelta(seconds=5)
        assert completed.version == 2

    asyncio.run(scenario())


def test_stale_lease_cannot_complete_success_failure_or_retry(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        [first] = await _claim_one(backend)
        clock.advance(seconds=31)
        [second] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-2")
        for status in (
            ExecutionStatus.SUCCEEDED,
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYING,
        ):
            with pytest.raises(StaleExecutionLease):
                await backend.complete(
                    first.execution.id,
                    first.attempt_id,
                    first.lease_token,
                    ExecutionCompletion(status=status),
                )
        assert second.execution.status is ExecutionStatus.RUNNING

    asyncio.run(scenario())


def test_release_unstarted_returns_claim_to_queue(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = MutableClock(datetime(2026, 8, 9, tzinfo=timezone.utc))
        backend = _backend(tmp_path / "lease.db", clock)
        [lease] = await _claim_one(backend)
        released = await backend.release(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
        )
        assert released.status is ExecutionStatus.QUEUED
        [reclaimed] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-2")
        assert reclaimed.execution.attempts == 2

    asyncio.run(scenario())


class FakeRuntimeBackend:
    class Connector:
        @staticmethod
        def secret_tokens() -> list[str]:
            return []

    connector = Connector()

    def __init__(self, lease: ExecutionLease) -> None:
        self.lease = lease
        self.claim_calls = []
        self.completions: list[ExecutionCompletion] = []
        self.heartbeat_called = asyncio.Event()
        self.remaining_lease_s: float | None = None
        self.remaining_lease_values: list[float] = []
        self.lease_remaining_calls = 0
        self.heartbeat_result = HeartbeatResult(
            lease_expires_at=lease.lease_expires_at,
            cancel_requested=False,
        )
        self.opened = False
        self.closed = False
        self.released = False

    async def open(self) -> None:
        self.opened = True

    async def close(self) -> None:
        self.closed = True

    async def claim(self, namespace, task_names, limit, lease_duration_s, worker_id):
        self.claim_calls.append((namespace, task_names, limit, lease_duration_s, worker_id))
        return (self.lease,)

    async def heartbeat(self, *args):
        self.heartbeat_called.set()
        return self.heartbeat_result

    async def lease_remaining(self, lease_expires_at):
        self.lease_remaining_calls += 1
        if self.remaining_lease_values:
            return self.remaining_lease_values.pop(0)
        if self.remaining_lease_s is not None:
            return self.remaining_lease_s
        return (lease_expires_at - datetime.now(timezone.utc)).total_seconds()

    async def complete(self, execution_id, attempt_id, lease_token, completion):
        self.completions.append(completion)
        return self.lease.execution

    async def release(self, *args):
        self.released = True
        return self.lease.execution


class FailingCompletionBackend(FakeRuntimeBackend):
    async def complete(self, execution_id, attempt_id, lease_token, completion):
        self.completions.append(completion)
        if len(self.completions) == 1:
            raise RuntimeError("completion failed")
        return self.lease.execution


class FailingClaimBackend(FakeRuntimeBackend):
    class Connector:
        @staticmethod
        def _secret_tokens() -> list[str]:
            return []

    connector = Connector()

    async def claim(self, namespace, task_names, limit, lease_duration_s, worker_id):
        raise sa.exc.OperationalError("SELECT 1", {}, Exception("connection reset"))


class TransientHeartbeatBackend(FakeRuntimeBackend):
    def __init__(self, lease: ExecutionLease) -> None:
        super().__init__(lease)
        self.heartbeat_attempts = 0

    async def heartbeat(self, *args):
        self.heartbeat_attempts += 1
        if self.heartbeat_attempts == 1:
            raise sa.exc.OperationalError(
                "UPDATE onestep_executions",
                {},
                Exception("connection reset"),
            )
        return await super().heartbeat(*args)


class MultiTransientHeartbeatBackend(FakeRuntimeBackend):
    def __init__(self, lease: ExecutionLease, failures: int) -> None:
        super().__init__(lease)
        self.failures = failures
        self.heartbeat_attempts = 0

    async def heartbeat(self, *args):
        self.heartbeat_attempts += 1
        if self.heartbeat_attempts <= self.failures:
            raise sa.exc.OperationalError(
                "UPDATE onestep_executions",
                {},
                Exception("connection reset"),
            )
        return await super().heartbeat(*args)


class AlwaysFailingHeartbeatBackend(FakeRuntimeBackend):
    def __init__(self, lease: ExecutionLease) -> None:
        super().__init__(lease)
        self.heartbeat_attempts = 0

    async def heartbeat(self, *args):
        self.heartbeat_attempts += 1
        raise sa.exc.OperationalError(
            "UPDATE onestep_executions",
            {},
            Exception("connection reset"),
        )


def _fake_lease(*, now: datetime | None = None) -> ExecutionLease:
    now = now or datetime(2026, 8, 9, tzinfo=timezone.utc)
    execution = Execution(
        id=uuid4(),
        namespace="agent-api",
        task_name="run_agent",
        status=ExecutionStatus.RUNNING,
        payload={"prompt": "hello"},
        metadata={"requested_by": "u-1"},
        result=None,
        error=None,
        attempts=1,
        created_at=now,
        available_at=now,
        started_at=now,
        finished_at=None,
        cancel_requested_at=None,
        expires_at=None,
        version=1,
    )
    return ExecutionLease(execution, uuid4(), uuid4(), now + timedelta(seconds=30))


def test_source_fetch_returns_managed_execution_delivery_with_correlation_meta() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            batch_size=2,
            poll_interval_s=0.1,
            lease_duration_s=30,
            heartbeat_interval_s=10,
            worker_id="worker-1",
        )
        await source.open()
        [delivery] = await source.fetch(5)
        assert isinstance(delivery, PostgresExecutionDelivery)
        assert delivery.payload == {"prompt": "hello"}
        assert delivery.envelope.attempts == 0
        assert delivery.envelope.meta["onestep.execution"]["id"] == str(lease.execution.id)
        assert backend.claim_calls[0][2] == 2
        assert backend.opened is True
        await source.close()
        assert backend.closed is True

    asyncio.run(scenario())


def test_source_fetch_normalizes_postgres_claim_errors() -> None:
    async def scenario() -> None:
        source = PostgresExecutionSource(
            backend=FailingClaimBackend(_fake_lease()),
            namespace="agent-api",
            task_names=("run_agent",),
            poll_interval_s=0.25,
            worker_id="worker-1",
        )

        with pytest.raises(ConnectorOperationError) as raised:
            await source.fetch(1)

        assert raised.value.backend == "postgres"
        assert raised.value.operation is ConnectorOperation.FETCH
        assert raised.value.kind is ConnectorErrorKind.DISCONNECTED
        assert raised.value.source_name == source.name
        assert raised.value.retry_delay_s == 0.25

    asyncio.run(scenario())


def test_delivery_start_processing_runs_heartbeat_until_completion() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            lease_duration_s=0.3,
            heartbeat_interval_s=0.1,
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        await delivery.start_processing()
        await asyncio.wait_for(backend.heartbeat_called.wait(), timeout=1)
        await delivery.complete_execution(ExecutionCompletion(status=ExecutionStatus.SUCCEEDED))
        assert backend.completions[0].status is ExecutionStatus.SUCCEEDED

    asyncio.run(scenario())


def test_delivery_retries_transient_heartbeat_failure() -> None:
    async def scenario() -> None:
        lease = _fake_lease(now=datetime.now(timezone.utc))
        backend = TransientHeartbeatBackend(lease)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            lease_duration_s=0.3,
            heartbeat_interval_s=0.1,
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )

        await delivery.start_processing()
        await asyncio.wait_for(backend.heartbeat_called.wait(), timeout=1)
        await delivery.complete_execution(
            ExecutionCompletion(status=ExecutionStatus.SUCCEEDED)
        )

        assert backend.heartbeat_attempts == 2

    asyncio.run(scenario())


def test_delivery_retries_heartbeat_failures_until_lease_window_allows_success() -> None:
    async def scenario() -> None:
        lease = _fake_lease(now=datetime.now(timezone.utc))
        backend = MultiTransientHeartbeatBackend(lease, failures=3)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            lease_duration_s=0.3,
            heartbeat_interval_s=0.1,
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )

        result = await delivery._heartbeat_with_retry()

        assert result is not None
        assert backend.heartbeat_attempts == 4

    asyncio.run(scenario())


def test_heartbeat_retry_backoff_is_clamped_to_remaining_lease(monkeypatch) -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = AlwaysFailingHeartbeatBackend(lease)
        backend.remaining_lease_values = [0.05, 0.04, 0.03, 0]
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            lease_duration_s=0.3,
            heartbeat_interval_s=0.1,
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        waits: list[float] = []

        async def fake_wait_for(awaitable, timeout):
            waits.append(timeout)
            awaitable.close()
            raise asyncio.TimeoutError

        monkeypatch.setattr(asyncio, "wait_for", fake_wait_for)
        assert await delivery._heartbeat_with_retry() is None
        assert backend.heartbeat_attempts == 4
        assert backend.lease_remaining_calls == 4
        assert waits
        assert max(waits) <= 0.05 / 4

    asyncio.run(scenario())


def test_delivery_completion_survives_pending_heartbeat_cancellation() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            lease_duration_s=0.3,
            heartbeat_interval_s=0.1,
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        await delivery.start_processing()

        delivery._cancel_owner()
        await delivery.complete_execution(
            ExecutionCompletion(
                status=ExecutionStatus.SUCCEEDED,
                result={"answer": 42},
            )
        )

        assert delivery._completed is True
        assert backend.completions[0].result == {"answer": 42}

    asyncio.run(scenario())


def test_heartbeat_loop_propagates_task_cancellation() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        source = PostgresExecutionSource(
            backend=FakeRuntimeBackend(lease),
            namespace="agent-api",
            task_names=("run_agent",),
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        heartbeat = asyncio.create_task(delivery._heartbeat_loop())
        await asyncio.sleep(0)
        heartbeat.cancel()

        with pytest.raises(asyncio.CancelledError):
            await heartbeat

    asyncio.run(scenario())


def test_legacy_ack_completes_success_with_none_result() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )

        await delivery.ack()

        assert backend.completions == [
            ExecutionCompletion(
                status=ExecutionStatus.SUCCEEDED,
                result=None,
            )
        ]

    asyncio.run(scenario())


def test_failed_completion_can_fall_back_to_retrying() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FailingCompletionBackend(lease)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )

        with pytest.raises(RuntimeError, match="completion failed"):
            await delivery.complete_execution(
                ExecutionCompletion(status=ExecutionStatus.FAILED)
            )
        await delivery.complete_execution(
            ExecutionCompletion(status=ExecutionStatus.RETRYING)
        )

        assert [item.status for item in backend.completions] == [
            ExecutionStatus.FAILED,
            ExecutionStatus.RETRYING,
        ]

    asyncio.run(scenario())


def test_heartbeat_cancel_request_cancels_owner_task() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        backend.heartbeat_result = HeartbeatResult(
            lease_expires_at=lease.lease_expires_at,
            cancel_requested=True,
        )
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            lease_duration_s=0.3,
            heartbeat_interval_s=0.1,
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        with pytest.raises(asyncio.CancelledError):
            async def owner() -> None:
                await delivery.start_processing()
                await backend.heartbeat_called.wait()
                await asyncio.sleep(1)

            await asyncio.wait_for(owner(), timeout=1)
        assert delivery.cancel_requested is True

    asyncio.run(scenario())


def test_worker_cancellation_without_business_cancel_completes_retrying() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            lease_duration_s=0.3,
            heartbeat_interval_s=0.1,
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        try:
            await delivery.start_processing()
            raise asyncio.CancelledError
        except asyncio.CancelledError:
            await delivery.complete_execution(
                ExecutionCompletion(status=ExecutionStatus.RETRYING, delay_s=0)
            )
        assert backend.completions[0].status is ExecutionStatus.RETRYING

    asyncio.run(scenario())


def test_business_cancel_completes_cancelled() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            lease_duration_s=0.3,
            heartbeat_interval_s=0.1,
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        delivery.cancel_requested = True
        await delivery.complete_execution(
            ExecutionCompletion(status=ExecutionStatus.CANCELLED)
        )
        assert backend.completions[0].status is ExecutionStatus.CANCELLED

    asyncio.run(scenario())


def test_source_release_unstarted_contract() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = PostgresExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            worker_id="worker-1",
        )
        delivery = PostgresExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        await delivery.release_unstarted()
        assert backend.released is True

    asyncio.run(scenario())

from __future__ import annotations

import asyncio
import base64
import inspect
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from onestep.execution import (
    ExecutionCompletion,
    ExecutionConflict,
    ExecutionEncodingError,
    ExecutionErrorDetail,
    ExecutionLeaseLost,
    ExecutionQuery,
    ExecutionRequest,
    ExecutionStatus,
    LeasedExecutionBackend,
)
from onestep_mysql import MySQLConnector
from onestep_mysql.execution_backend import (
    MySQLExecutionBackend,
    MySQLExecutionDialect,
    StaleExecutionLease,
    _is_mysql_dsn,
    _pin_mysql_execution_engine,
    _pin_mysql_execution_session,
)
from onestep_sql._shared.execution.machine import StaleExecutionLease as SharedStaleExecutionLease


NOW = datetime(2026, 8, 9, tzinfo=timezone.utc)


def _backend(path: Path, *, auto_create: bool = True, clock=None) -> MySQLExecutionBackend:
    connector = MySQLConnector(f"sqlite:///{path}")
    return MySQLExecutionBackend(
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


# -- construction, lifecycle and connector ownership --------------------------


def test_execution_backend_signature_matches_postgres_param_for_param() -> None:
    from onestep_sql.postgres.connector import PostgresConnector

    mysql_signature = inspect.signature(MySQLConnector.execution_backend)
    postgres_signature = inspect.signature(PostgresConnector.execution_backend)
    assert list(mysql_signature.parameters) == list(postgres_signature.parameters)
    for name, mysql_param in mysql_signature.parameters.items():
        postgres_param = postgres_signature.parameters[name]
        assert mysql_param.kind == postgres_param.kind
        assert mysql_param.default == postgres_param.default
    # All seven documented parameters (design §10.1) are covered above; assert
    # the count explicitly so a silently dropped parameter cannot pass.
    assert len([n for n in mysql_signature.parameters if n != "self"]) == 7


def test_public_naming_is_backend_specific() -> None:
    assert MySQLExecutionBackend.__name__ == "MySQLExecutionBackend"
    assert issubclass(MySQLExecutionBackend, LeasedExecutionBackend)
    import onestep_sql.mysql.execution_backend as module

    assert "SQLExecutionBackend" not in module.__all__


def test_stale_lease_keeps_the_shared_identity() -> None:
    # onestep_mysql.execution_backend forwards to this module; the raised
    # exception type must be the exact shared machine object.
    assert StaleExecutionLease is SharedStaleExecutionLease
    assert issubclass(StaleExecutionLease, ExecutionLeaseLost)


def test_both_import_paths_resolve_to_the_same_objects() -> None:
    import onestep_mysql.execution_backend as shim_backend
    import onestep_mysql.execution_schema as shim_schema
    from onestep_sql.mysql.execution_schema import build_execution_tables

    assert shim_backend.MySQLExecutionBackend is MySQLExecutionBackend
    assert shim_backend.MySQLExecutionDialect is MySQLExecutionDialect
    assert shim_schema.build_execution_tables is build_execution_tables


def test_dialect_satisfies_the_shared_seam_protocol() -> None:
    from onestep_sql._shared.execution.dialect import ExecutionDialect

    dialect = MySQLExecutionDialect()
    assert isinstance(dialect, ExecutionDialect)
    assert dialect.name == "mysql"
    assert MySQLExecutionBackend._dialect_cls is MySQLExecutionDialect


def test_direct_dsn_backend_lazily_creates_and_reopens_owned_connector(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        backend = MySQLExecutionBackend(
            dsn=f"sqlite:///{tmp_path / 'direct.db'}",
            auto_create=True,
        )
        assert backend.connector is None

        await backend.open()
        first_connector = backend.connector
        assert first_connector is not None
        await backend.open()

        await backend.close()
        assert backend.connector is first_connector

        await backend.close()
        assert backend.connector is None

        await backend.open()
        assert backend.connector is not first_connector
        await backend.close()

    asyncio.run(scenario())


def test_backend_from_connector_preserves_external_ownership(tmp_path: Path) -> None:
    async def scenario() -> None:
        connector = MySQLConnector(f"sqlite:///{tmp_path / 'shared.db'}")
        backend = MySQLExecutionBackend.from_connector(
            connector,
            auto_create=True,
        )
        compatibility_backend = connector.execution_backend(auto_create=True)

        assert backend.connector is connector
        assert compatibility_backend.connector is connector

        await backend.open()
        await backend.close()
        assert backend.connector is connector
        await connector.close()

    asyncio.run(scenario())


def test_owned_backend_rebuilds_pool_after_process_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = MySQLExecutionBackend(
            dsn=f"sqlite:///{tmp_path / 'fork-safe.db'}",
            auto_create=True,
        )
        await backend.open()
        first_connector = backend.connector
        first_engine = backend.engine
        assert first_connector is not None

        backend._pid -= 1
        await backend.open()

        assert backend.connector is not first_connector
        assert backend.engine is not first_engine
        await backend.close()

    asyncio.run(scenario())


def test_external_connector_rejects_use_after_process_boundary(tmp_path: Path) -> None:
    async def scenario() -> None:
        connector = MySQLConnector(f"sqlite:///{tmp_path / 'external-fork.db'}")
        backend = MySQLExecutionBackend.from_connector(connector)
        backend._pid -= 1

        with pytest.raises(RuntimeError, match="externally supplied connector"):
            await backend.open()

        await connector.close()

    asyncio.run(scenario())


def test_open_without_auto_create_reports_missing_tables(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "missing.db", auto_create=False)
        with pytest.raises(RuntimeError, match="missing execution tables"):
            await backend.open()

    asyncio.run(scenario())


# -- dialect seam (design §11.1) ----------------------------------------------


def test_transaction_now_defers_to_the_injected_clock(tmp_path: Path) -> None:
    async def scenario() -> None:
        # §6.5: MySQL's NOW(6) is not transaction-stable, so the dialect must
        # return None and the machine must fall back to the injected clock —
        # every lease check in one transaction then agrees on a single instant.
        assert await MySQLExecutionDialect().transaction_now(None) is None
        clock_value = NOW + timedelta(seconds=3)
        backend = _backend(tmp_path / "clock.db", clock=lambda: clock_value)
        assert await backend._transaction_now(None) == clock_value

    asyncio.run(scenario())


def test_normalize_datetime_converts_offsets_to_utc() -> None:
    dialect = MySQLExecutionDialect()
    plus_eight = timezone(timedelta(hours=8))
    value = datetime(2026, 9, 15, 12, 27, 26, tzinfo=plus_eight)
    normalized = dialect.normalize_datetime(value)
    # §6.3: MySQL discards the offset of a bound DATETIME; the write boundary
    # must convert instead, or an already-expired expires_at looks claimable.
    assert normalized.tzinfo is timezone.utc
    assert normalized == datetime(2026, 9, 15, 4, 27, 26, tzinfo=timezone.utc)


def test_normalize_datetime_rejects_naive_datetimes() -> None:
    dialect = MySQLExecutionDialect()
    with pytest.raises(ValueError, match="timezone-aware"):
        dialect.normalize_datetime(datetime(2026, 9, 15, 12, 27, 26))


def test_is_mysql_dsn_drives_the_dialect_guard() -> None:
    assert _is_mysql_dsn("mysql://root:root@127.0.0.1:3306/onestep")
    assert _is_mysql_dsn("mysql+pymysql://root:root@127.0.0.1:3306/onestep")
    assert _is_mysql_dsn("mysql+asyncmy://root:root@127.0.0.1:3306/onestep")
    assert not _is_mysql_dsn("sqlite://")
    assert not _is_mysql_dsn("sqlite:////tmp/x.db")
    assert not _is_mysql_dsn("postgresql://localhost/db")


def test_session_pinning_is_skipped_for_non_mysql_engines(tmp_path: Path) -> None:
    async def scenario() -> None:
        connector = MySQLConnector(f"sqlite:///{tmp_path / 'sqlite-guard.db'}")
        backend = MySQLExecutionBackend.from_connector(connector, auto_create=True)
        # §11.1: SET time_zone / READ COMMITTED are MySQL-only engine options;
        # the sqlite unit path must stay untouched.
        assert backend.engine.dialect.name == "sqlite"
        assert not sa.event.contains(
            backend.engine.sync_engine, "connect", _pin_mysql_execution_session
        )
        await backend.open()
        # The sqlite path still works end to end.
        submitted = await backend.submit(_request())
        assert submitted.status is ExecutionStatus.QUEUED
        await backend.close()
        await connector.close()

    asyncio.run(scenario())


def test_session_pinning_attaches_to_mysql_dialect_engines() -> None:
    async def scenario() -> None:
        # A lazily-created MySQL-dialect engine (no connection is made) proves
        # the guard bookkeeping the real path relies on: the listener attaches,
        # a repeated attach is a no-op, and non-MySQL engines are untouched.
        engine = create_async_engine("mysql+asyncmy://user:pw@127.0.0.1:3306/db")
        try:
            assert engine.dialect.name == "mysql"
            _pin_mysql_execution_engine(engine)
            assert sa.event.contains(
                engine.sync_engine, "connect", _pin_mysql_execution_session
            )
            _pin_mysql_execution_engine(engine)
            assert sa.event.contains(
                engine.sync_engine, "connect", _pin_mysql_execution_session
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


# -- state machine parity on the MySQL schema (sqlite unit path) --------------


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
        async with backend.engine.begin() as conn:
            await conn.execute(
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
        async with backend.engine.begin() as conn:
            await conn.execute(
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
        async with backend.engine.begin() as conn:
            await conn.execute(
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

        async with backend.engine.begin() as conn:
            await conn.execute(
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


def test_list_rejects_malformed_and_noncanonical_cursors(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        execution = await backend.submit(_request())
        canonical = backend._encode_cursor(execution.created_at, execution.id)
        noncanonical_json = base64.urlsafe_b64encode(
            json.dumps(
                {
                    "v": 1,
                    "created_at": execution.created_at.isoformat(),
                    "id": str(execution.id),
                }
            ).encode()
        ).decode().rstrip("=")
        invalid_values = (
            "not-base64!",
            f"{canonical}=",
            noncanonical_json,
            base64.urlsafe_b64encode(b"[]").decode().rstrip("="),
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "v": True,
                        "created_at": execution.created_at.isoformat(),
                        "id": str(execution.id),
                    },
                    separators=(",", ":"),
                ).encode()
            ).decode().rstrip("="),
            "x" * 1025,
        )

        for value in invalid_values:
            with pytest.raises(ValueError, match="invalid execution cursor"):
                await backend.list(
                    ExecutionQuery(namespace="agent-api", cursor=value)
                )

    asyncio.run(scenario())


def test_claim_lease_heartbeat_and_fencing_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = {"now": NOW}
        backend = _backend(tmp_path / "execution.db", clock=lambda: clock["now"])
        submitted = await backend.submit(_request())

        [lease] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
        assert lease.execution.id == submitted.id
        assert lease.execution.status is ExecutionStatus.RUNNING
        assert lease.execution.attempts == 1

        clock["now"] = NOW + timedelta(seconds=1)
        heartbeat = await backend.heartbeat(
            lease.execution.id, lease.attempt_id, lease.lease_token, 30
        )
        assert heartbeat.cancel_requested is False
        assert heartbeat.lease_expires_at > lease.lease_expires_at

        completed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"ok": 1}),
        )
        assert completed.status is ExecutionStatus.SUCCEEDED
        assert completed.result == {"ok": 1}

        # Terminal replay with the same result is idempotent; a different
        # result for the same lease is rejected.
        replayed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"ok": 1}),
        )
        assert replayed.status is ExecutionStatus.SUCCEEDED
        with pytest.raises(StaleExecutionLease):
            await backend.complete(
                lease.execution.id,
                lease.attempt_id,
                lease.lease_token,
                ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"ok": 2}),
            )

    asyncio.run(scenario())


def test_stale_heartbeat_is_rejected_after_lease_expiry(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = {"now": NOW}
        backend = _backend(tmp_path / "execution.db", clock=lambda: clock["now"])
        await backend.submit(_request())
        [lease] = await backend.claim("agent-api", ("run_agent",), 1, 1, "worker-a")

        clock["now"] = NOW + timedelta(seconds=2)
        with pytest.raises(StaleExecutionLease):
            await backend.heartbeat(
                lease.execution.id, lease.attempt_id, lease.lease_token, 30
            )

    asyncio.run(scenario())


def test_release_returns_the_execution_to_queued(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        await backend.submit(_request())
        [lease] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
        released = await backend.release(
            lease.execution.id, lease.attempt_id, lease.lease_token
        )
        assert released.status is ExecutionStatus.QUEUED
        assert released.attempts == 1
        [reclaimed] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-b")
        assert reclaimed.execution.id == lease.execution.id
        assert reclaimed.execution.attempts == 2

    asyncio.run(scenario())


def test_expired_lease_takeover_fences_the_old_worker(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = {"now": NOW}
        backend = _backend(tmp_path / "execution.db", clock=lambda: clock["now"])
        await backend.submit(_request())
        [old] = await backend.claim("agent-api", ("run_agent",), 1, 0.5, "worker-a")

        clock["now"] = NOW + timedelta(seconds=1)
        [new] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-b")
        assert new.execution.id == old.execution.id
        assert new.execution.attempts == 2
        assert new.lease_token != old.lease_token

        # The zombie worker's completion must not win (fencing).
        with pytest.raises(StaleExecutionLease):
            await backend.complete(
                old.execution.id,
                old.attempt_id,
                old.lease_token,
                ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"zombie": True}),
            )
        async with backend.engine.begin() as conn:
            attempt_status = (await conn.execute(
                sa.select(backend.tables.attempts.c.status).where(
                    backend.tables.attempts.c.id == old.attempt_id
                )
            )).scalar_one()
        assert attempt_status == "lease_lost"

    asyncio.run(scenario())


def test_cancel_beats_a_late_success(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        submitted = await backend.submit(_request())
        [lease] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
        requested = await backend.request_cancel("agent-api", submitted.id, reason="stop")
        assert requested is not None
        assert requested.status is ExecutionStatus.CANCEL_REQUESTED

        completed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            ExecutionCompletion(status=ExecutionStatus.SUCCEEDED, result={"late": True}),
        )
        assert completed.status is ExecutionStatus.CANCELLED
        assert completed.result is None

    asyncio.run(scenario())


def test_retrying_completion_schedules_the_next_attempt(tmp_path: Path) -> None:
    async def scenario() -> None:
        clock = {"now": NOW}
        backend = _backend(tmp_path / "execution.db", clock=lambda: clock["now"])
        await backend.submit(_request())
        [lease] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
        clock["now"] = NOW + timedelta(seconds=5)
        retried = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            ExecutionCompletion(status=ExecutionStatus.RETRYING, delay_s=60),
        )
        assert retried.status is ExecutionStatus.RETRYING
        assert retried.available_at == NOW + timedelta(seconds=65)

    asyncio.run(scenario())


def test_failed_completion_persists_the_error(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        await backend.submit(_request())
        [lease] = await backend.claim("agent-api", ("run_agent",), 1, 30, "worker-a")
        failed = await backend.complete(
            lease.execution.id,
            lease.attempt_id,
            lease.lease_token,
            ExecutionCompletion(
                status=ExecutionStatus.FAILED,
                error=ExecutionErrorDetail(kind="handler", exception_type="ValueError"),
            ),
        )
        assert failed.status is ExecutionStatus.FAILED
        assert failed.error is not None
        assert failed.error.exception_type == "ValueError"

    asyncio.run(scenario())


def test_expired_expires_at_rows_are_not_claimed(tmp_path: Path) -> None:
    async def scenario() -> None:
        backend = _backend(tmp_path / "execution.db")
        await backend.submit(
            _request(expires_at=NOW - timedelta(hours=1))
        )
        assert await backend.claim("agent-api", ("run_agent",), 5, 30, "worker-a") == ()
        page = await backend.list(
            ExecutionQuery(namespace="agent-api", status=ExecutionStatus.EXPIRED, limit=10)
        )
        assert len(page.items) == 1

    asyncio.run(scenario())

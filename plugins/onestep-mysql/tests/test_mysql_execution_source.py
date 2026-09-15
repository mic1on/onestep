"""Unit tests for the MySQL tracked execution source and delivery (Phase 3).

Mirrors the PostgreSQL source suite (``onestep-postgres``:
``test_postgres_execution_source.py``) for the parts that live in the MySQL
binding: backend-named symbols, the shared option validator, error
normalization through the MySQL error factory, envelope correlation metadata,
and the ``mysql_execution_source`` YAML resource with its strict validation.
The claim/lease/fencing state machine itself is dialect-independent and is
exhaustively covered by ``test_postgres_execution_source.py`` (PostgreSQL) and
``test_mysql_execution_backend.py`` (MySQL backend), so only the seams are
re-proved here.

Everything runs on ``sqlite:///`` through ``MySQLConnector`` (design §11.1);
no live MySQL required.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from onestep import OneStepApp
from onestep.config import load_app_config
from onestep.envelope import Envelope
from onestep.execution import (
    Execution,
    ExecutionClient,
    ExecutionCompletion,
    ExecutionLease,
    ExecutionRequest,
    ExecutionStatus,
    HeartbeatResult,
)
from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)
from onestep.runtime import TaskRunner
from onestep_mysql import MySQLConnector
from onestep_mysql.execution_source import (
    MySQLExecutionDelivery,
    MySQLExecutionSource,
)
from onestep_postgres import PostgresConnector
from onestep_sql._shared.execution.source import (
    ExecutionDeliveryBase,
    ExecutionSourceBase,
)
from onestep_sql.mysql import execution_source as canonical_execution_source


def test_public_naming_is_backend_specific() -> None:
    assert MySQLExecutionSource._source_kind == "mysql.execution"
    assert MySQLExecutionSource._backend_cls.__name__ == "MySQLExecutionBackend"
    assert MySQLExecutionSource._delivery_cls is MySQLExecutionDelivery
    assert issubclass(MySQLExecutionSource, ExecutionSourceBase)
    assert issubclass(MySQLExecutionDelivery, ExecutionDeliveryBase)


def test_shim_import_path_is_the_canonical_module() -> None:
    import onestep_mysql.execution_source as shim_module

    assert shim_module is canonical_execution_source
    assert shim_module.MySQLExecutionSource is MySQLExecutionSource
    assert shim_module.MySQLExecutionDelivery is MySQLExecutionDelivery


def test_source_rejects_multiple_task_names() -> None:
    with pytest.raises(ValueError, match="exactly one task name"):
        MySQLExecutionSource(
            backend=object(),
            namespace="agent-api",
            task_names=("task_a", "task_b"),
            worker_id="worker-1",
        )


def test_source_rejects_task_name_mismatch() -> None:
    source = MySQLExecutionSource(
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
        MySQLExecutionSource(**source_options)
    with pytest.raises(ValueError, match="exactly one of dsn or backend"):
        MySQLExecutionSource(
            dsn="sqlite:///unused.db",
            backend=object(),
            **source_options,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("batch_size", 0),
        ("poll_interval_s", 0),
        ("poll_interval_s", float("nan")),
        ("lease_duration_s", 0),
        ("lease_duration_s", float("inf")),
        ("heartbeat_interval_s", 31),
        ("worker_id", "  "),
        ("namespace", "x" * 256),
    ],
)
def test_python_source_uses_shared_validation(field, value, tmp_path) -> None:
    options = {
        "namespace": "agent-api",
        "task_names": ("run_agent",),
        "worker_id": "worker-1",
    }
    options[field] = value
    if field == "heartbeat_interval_s":
        options["lease_duration_s"] = 90

    with pytest.raises((TypeError, ValueError), match=field):
        MySQLExecutionSource(dsn=f"sqlite:///{tmp_path / 'python-validation.db'}", **options)


def test_direct_dsn_source_lazily_owns_backend_lifecycle(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = MySQLExecutionSource(
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


# -- fake runtime backends (mirroring the PostgreSQL suite's helpers) --------


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


class FakeRuntimeBackend:
    class Connector:
        @staticmethod
        def secret_tokens() -> list[str]:
            return []

    connector = Connector()

    def __init__(self, lease: ExecutionLease) -> None:
        self.lease = lease
        self.claim_calls = []
        self.completions = []
        self.heartbeat_called = asyncio.Event()
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
        return (lease_expires_at - datetime.now(timezone.utc)).total_seconds()

    async def complete(self, execution_id, attempt_id, lease_token, completion):
        self.completions.append(completion)
        return self.lease.execution

    async def release(self, *args):
        self.released = True
        return self.lease.execution


class FailingClaimBackend(FakeRuntimeBackend):
    class Connector:
        @staticmethod
        def _secret_tokens() -> list[str]:
            return []

    connector = Connector()

    async def claim(self, namespace, task_names, limit, lease_duration_s, worker_id):
        raise sa.exc.OperationalError("SELECT 1", {}, Exception("connection reset"))


def test_source_fetch_returns_mysql_delivery_with_correlation_meta() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = MySQLExecutionSource(
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
        assert isinstance(delivery, MySQLExecutionDelivery)
        assert delivery.payload == {"prompt": "hello"}
        assert delivery.envelope.attempts == 0
        assert delivery.envelope.meta["onestep.execution"]["id"] == str(lease.execution.id)
        assert delivery.envelope.meta["onestep.execution"]["attempt_id"] == str(lease.attempt_id)
        assert backend.claim_calls[0][0] == "agent-api"
        assert backend.claim_calls[0][2] == 2
        assert backend.opened is True
        await source.close()
        assert backend.closed is True

    asyncio.run(scenario())


def test_source_fetch_normalizes_mysql_claim_errors() -> None:
    async def scenario() -> None:
        source = MySQLExecutionSource(
            backend=FailingClaimBackend(_fake_lease()),
            namespace="agent-api",
            task_names=("run_agent",),
            poll_interval_s=0.25,
            worker_id="worker-1",
        )

        with pytest.raises(ConnectorOperationError) as raised:
            await source.fetch(1)

        assert raised.value.backend == "mysql"
        assert raised.value.operation is ConnectorOperation.FETCH
        assert raised.value.kind is ConnectorErrorKind.TRANSIENT
        assert raised.value.source_name == source.name
        assert raised.value.retry_delay_s == 0.25

    asyncio.run(scenario())


def test_delivery_start_processing_runs_heartbeat_until_completion() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = MySQLExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            lease_duration_s=0.3,
            heartbeat_interval_s=0.1,
            worker_id="worker-1",
        )
        delivery = MySQLExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        await delivery.start_processing()
        await asyncio.wait_for(backend.heartbeat_called.wait(), timeout=1)
        await delivery.complete_execution(ExecutionCompletion(status=ExecutionStatus.SUCCEEDED))
        assert backend.completions[0].status is ExecutionStatus.SUCCEEDED

    asyncio.run(scenario())


def test_delivery_release_unstarted_calls_backend_release() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = MySQLExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            worker_id="worker-1",
        )
        delivery = MySQLExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        await delivery.release_unstarted()
        assert backend.released is True

    asyncio.run(scenario())


def test_legacy_ack_completes_success_with_none_result() -> None:
    async def scenario() -> None:
        lease = _fake_lease()
        backend = FakeRuntimeBackend(lease)
        source = MySQLExecutionSource(
            backend=backend,
            namespace="agent-api",
            task_names=("run_agent",),
            worker_id="worker-1",
        )
        delivery = MySQLExecutionDelivery(
            source=source,
            lease=lease,
            envelope=Envelope(body=lease.execution.payload),
        )
        await delivery.ack()
        [completion] = backend.completions
        assert completion.status is ExecutionStatus.SUCCEEDED
        assert completion.result is None

    asyncio.run(scenario())


# -- mysql_execution_source YAML resource ------------------------------------


def _app_config(dsn: str, jobs: dict) -> dict:
    return {
        "apiVersion": "onestep/v1alpha1",
        "kind": "App",
        "app": {"name": "mysql-execution-plugin"},
        "resources": {
            "db": {"type": "mysql", "dsn": dsn},
            "jobs": jobs,
        },
        "tasks": [],
    }


def test_strict_yaml_builds_execution_source_with_shared_connector(tmp_path) -> None:
    dsn = f"sqlite:///{tmp_path / 'mysql-execution-plugin.db'}"
    app = load_app_config(
        _app_config(
            dsn,
            {
                "type": "mysql_execution_source",
                "connector": "db",
                "namespace": "agent-api",
                "task_names": ["run_agent"],
                "worker_id": "worker-1",
                "reclaim_batch_size": 7,
            },
        ),
        strict=True,
    )
    assert isinstance(app.resources["jobs"], MySQLExecutionSource)
    assert app.resources["jobs"].backend.connector is app.resources["db"]
    assert app.resources["jobs"].backend.reclaim_batch_size == 7
    assert app.resources["jobs"].backend.table_name == "onestep_executions"
    assert app.resources["jobs"].backend.attempts_table_name == "onestep_execution_attempts"


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("task_names", [], "task_names"),
        ("task_names", ["task_a", "task_b"], "exactly one task name"),
        ("batch_size", 0, "batch_size"),
        ("poll_interval_s", 0, "poll_interval_s"),
        ("poll_interval_s", float("nan"), "poll_interval_s"),
        ("lease_duration_s", 0, "lease_duration_s"),
        ("lease_duration_s", float("inf"), "lease_duration_s"),
        ("heartbeat_interval_s", 31, "heartbeat_interval_s"),
        ("reclaim_batch_size", 0, "reclaim_batch_size"),
    ],
)
def test_strict_execution_source_validation_is_field_qualified(
    field,
    value,
    match,
    tmp_path,
) -> None:
    spec = {
        "type": "mysql_execution_source",
        "connector": "db",
        "namespace": "agent-api",
        "task_names": ["run_agent"],
        field: value,
    }
    if field == "heartbeat_interval_s":
        spec["lease_duration_s"] = 90
    with pytest.raises((TypeError, ValueError), match=match):
        load_app_config(
            _app_config(f"sqlite:///{tmp_path / 'invalid.db'}", spec),
            strict=True,
        )


def test_strict_execution_source_rejects_unknown_fields(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        load_app_config(
            _app_config(
                f"sqlite:///{tmp_path / 'unknown.db'}",
                {
                    "type": "mysql_execution_source",
                    "connector": "db",
                    "namespace": "agent-api",
                    "task_names": ["run_agent"],
                    "unknown": True,
                },
            ),
            strict=True,
        )


def test_execution_source_requires_mysql_connector(tmp_path) -> None:
    with pytest.raises(TypeError, match="must be a MySQLConnector"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "invalid-mysql-dependency"},
                "resources": {
                    "queue": {"type": "memory", "maxsize": 1},
                    "jobs": {
                        "type": "mysql_execution_source",
                        "connector": "queue",
                        "namespace": "agent-api",
                        "task_names": ["run_agent"],
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_cross_backend_connector_is_rejected_in_both_directions(tmp_path) -> None:
    # mysql_execution_source fed a PostgreSQL connector must be rejected...
    with pytest.raises(TypeError, match="must be a MySQLConnector"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "cross-backend-mysql"},
                "resources": {
                    "db": {"type": "postgres", "dsn": f"sqlite:///{tmp_path / 'pg.db'}"},
                    "jobs": {
                        "type": "mysql_execution_source",
                        "connector": "db",
                        "namespace": "agent-api",
                        "task_names": ["run_agent"],
                    },
                },
                "tasks": [],
            },
            strict=True,
        )
    # ...and postgres_execution_source fed a MySQL connector must be rejected
    # too: neither backend offers a generic connector (consolidation §6).
    with pytest.raises(TypeError, match="must be a PostgresConnector"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "cross-backend-postgres"},
                "resources": {
                    "db": {"type": "mysql", "dsn": f"sqlite:///{tmp_path / 'my.db'}"},
                    "jobs": {
                        "type": "postgres_execution_source",
                        "connector": "db",
                        "namespace": "agent-api",
                        "task_names": ["run_agent"],
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


# -- YAML end-to-end on sqlite: submit → source claim → result ---------------


def test_yaml_e2e_submit_claim_complete_on_sqlite(tmp_path: Path) -> None:
    """Runnable YAML pipeline: OneStepApp registers a ``mysql_execution_source``
    built by strict YAML, a submitted execution is claimed through the source
    and completed by the runtime, and the API client reads the result back."""

    async def scenario() -> None:
        dsn = f"sqlite:///{tmp_path / 'yaml-e2e.db'}"
        app = load_app_config(
            _app_config(
                dsn,
                {
                    "type": "mysql_execution_source",
                    "connector": "db",
                    "namespace": "agent-api",
                    "task_names": ["run_agent"],
                    "worker_id": "yaml-worker-1",
                },
            ),
            strict=True,
        )
        source = app.resources["jobs"]
        assert isinstance(source, MySQLExecutionSource)

        await source.open()
        try:
            client = ExecutionClient(source.backend, namespace="agent-api")
            submitted = await client.submit(
                "run_agent", {"prompt": "hello"}, idempotency_key="yaml-e2e-1"
            )
            assert submitted.status is ExecutionStatus.QUEUED

            [delivery] = await source.fetch(1)

            @app.task(name="run_agent", source=source)
            async def run_agent(ctx, payload):
                return {"answer": 42}

            await TaskRunner(app, app.tasks[0])._handle_delivery(delivery)

            result = await client.result(submitted.id)
            assert result == {"answer": 42}
            final = await client.get(submitted.id)
            assert final is not None
            assert final.status is ExecutionStatus.SUCCEEDED
        finally:
            await source.close()

    asyncio.run(scenario())

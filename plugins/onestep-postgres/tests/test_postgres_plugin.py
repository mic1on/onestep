from __future__ import annotations

import asyncio
import importlib
from importlib import metadata as importlib_metadata
from typing import Any

import pytest

from onestep.config import load_app_config
from onestep.resilience import ConnectorErrorKind, ConnectorOperation, ConnectorOperationError
from onestep.resource_registry import ResourceRegistry
from onestep_postgres import (
    PostgresConnector,
    PostgresExecutionBackend,
    PostgresExecutionSource,
    PostgresIncrementalSource,
    PostgresTableSink,
    SQLAlchemyCursorStore,
    SQLAlchemyStateStore,
    register,
)
from onestep_postgres.resilience import (
    PostgresErrorCause,
    as_postgres_connector_operation_error,
    classify_sqlalchemy_error,
)


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "postgres"
        and entry_point.value == "onestep_postgres:register"
        for entry_point in entry_points
    )


def test_postgres_plugin_registers_catalog_metadata() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    connector_fields = {field.name: field for field in catalog["postgres"].fields}

    assert catalog["postgres"].roles == ("connector",)
    assert connector_fields["dsn"].required is True
    assert connector_fields["dsn"].secret is True
    assert connector_fields["password"].secret is True
    assert catalog["postgres_incremental"].roles == ("source",)
    assert catalog["postgres_incremental"].connector_types == ("postgres",)
    assert catalog["postgres_table_sink"].roles == ("sink",)
    assert catalog["postgres_table_sink"].connector_types == ("postgres",)
    assert catalog["postgres_execution_source"].roles == ("source",)
    assert catalog["postgres_execution_source"].connector_types == ("postgres",)
    execution_fields = {
        field.name: field for field in catalog["postgres_execution_source"].fields
    }
    assert execution_fields["reclaim_batch_size"].default == 100


def test_postgres_connector_builds_execution_backend_and_source(tmp_path) -> None:
    connector = PostgresConnector(f"sqlite:///{tmp_path / 'execution.db'}")
    backend = connector.execution_backend(
        auto_create=True,
        reclaim_batch_size=7,
    )
    source = backend.source(
        namespace="agent-api",
        task_names=("run_agent",),
        worker_id="worker-1",
    )
    assert isinstance(backend, PostgresExecutionBackend)
    assert backend.reclaim_batch_size == 7
    assert isinstance(source, PostgresExecutionSource)


def test_app_task_must_match_postgres_execution_source(tmp_path) -> None:
    from onestep import OneStepApp

    connector = PostgresConnector(f"sqlite:///{tmp_path / 'task-binding.db'}")
    source = connector.execution_backend().source(
        namespace="agent-api",
        task_names=("task_a",),
        worker_id="worker-1",
    )
    app = OneStepApp("task-binding")

    with pytest.raises(ValueError, match="configured for task 'task_a'"):
        @app.task(name="task_b", source=source)
        async def task_b(ctx, payload):
            return payload


def test_sqlalchemy_state_store_is_not_exposed_by_core() -> None:
    import onestep

    assert not hasattr(onestep, "SQLAlchemyStateStore")
    assert not hasattr(onestep, "SQLAlchemyCursorStore")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("onestep.state_sqlalchemy")


def test_postgres_plugin_normalizes_sqlalchemy_errors() -> None:
    assert classify_sqlalchemy_error(TimeoutError("timeout")) is None

    from onestep_postgres.resilience import sa

    assert sa is not None
    sql_error = sa.exc.TimeoutError("timeout")
    assert classify_sqlalchemy_error(sql_error) is ConnectorErrorKind.TRANSIENT

    normalized = as_postgres_connector_operation_error(
        operation=ConnectorOperation.FETCH,
        exc=sql_error,
        source_name="postgres.incremental:users",
        retry_delay_s=2.0,
    )

    assert isinstance(normalized, ConnectorOperationError)
    assert normalized.backend == "postgres"
    assert normalized.operation is ConnectorOperation.FETCH
    assert normalized.kind is ConnectorErrorKind.TRANSIENT
    assert normalized.source_name == "postgres.incremental:users"
    assert normalized.retry_delay_s == 2.0
    assert isinstance(normalized.cause, PostgresErrorCause)
    assert "timeout" in str(normalized.cause)


def test_yaml_builds_postgres_resources_via_plugin_entry_point(tmp_path) -> None:
    dsn = f"sqlite:///{tmp_path / 'postgres-plugin.db'}"

    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {
                "name": "postgres-plugin",
                "state": "app_state",
            },
            "resources": {
                "db": {
                    "type": "postgres",
                    "dsn": dsn,
                },
                "app_state": {
                    "type": "postgres_state_store",
                    "connector": "db",
                },
                "cursor": {
                    "type": "postgres_cursor_store",
                    "connector": "db",
                },
                "users": {
                    "type": "postgres_incremental",
                    "connector": "db",
                    "table": "users",
                    "key": "id",
                    "cursor": ["updated_at", "id"],
                    "state": "cursor",
                },
                "processed": {
                    "type": "postgres_table_sink",
                    "connector": "db",
                    "table": "processed_users",
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    assert isinstance(app.resources["db"], PostgresConnector)
    assert isinstance(app.resources["app_state"], SQLAlchemyStateStore)
    assert isinstance(app.resources["cursor"], SQLAlchemyCursorStore)
    assert isinstance(app.resources["users"], PostgresIncrementalSource)
    assert app.resources["users"].state is app.resources["cursor"]
    assert isinstance(app.resources["processed"], PostgresTableSink)
    assert app.state is app.resources["app_state"]


def test_strict_yaml_builds_execution_source_with_shared_connector(tmp_path) -> None:
    dsn = f"sqlite:///{tmp_path / 'postgres-execution-plugin.db'}"
    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "postgres-execution-plugin"},
            "resources": {
                "db": {"type": "postgres", "dsn": dsn},
                "jobs": {
                    "type": "postgres_execution_source",
                    "connector": "db",
                    "namespace": "agent-api",
                    "task_names": ["run_agent"],
                    "worker_id": "worker-1",
                    "reclaim_batch_size": 7,
                },
            },
            "tasks": [],
        },
        strict=True,
    )
    assert isinstance(app.resources["jobs"], PostgresExecutionSource)
    assert app.resources["jobs"].backend.connector is app.resources["db"]
    assert app.resources["jobs"].backend.reclaim_batch_size == 7


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
        "type": "postgres_execution_source",
        "connector": "db",
        "namespace": "agent-api",
        "task_names": ["run_agent"],
        field: value,
    }
    if field == "heartbeat_interval_s":
        spec["lease_duration_s"] = 90
    with pytest.raises((TypeError, ValueError), match=match):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "invalid-postgres-execution"},
                "resources": {
                    "db": {
                        "type": "postgres",
                        "dsn": f"sqlite:///{tmp_path / 'invalid.db'}",
                    },
                    "jobs": spec,
                },
                "tasks": [],
            },
            strict=True,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("batch_size", 0),
        ("poll_interval_s", float("nan")),
        ("lease_duration_s", float("inf")),
        ("heartbeat_interval_s", 31),
    ],
)
def test_python_execution_source_uses_shared_validation(
    field,
    value,
    tmp_path,
) -> None:
    connector = PostgresConnector(f"sqlite:///{tmp_path / 'python-validation.db'}")
    backend = connector.execution_backend()
    options = {
        "namespace": "agent-api",
        "task_names": ("run_agent",),
        "worker_id": "worker-1",
    }
    options[field] = value
    if field == "heartbeat_interval_s":
        options["lease_duration_s"] = 90

    with pytest.raises((TypeError, ValueError), match=field):
        backend.source(**options)


def test_execution_backend_rejects_invalid_reclaim_batch_size(tmp_path) -> None:
    connector = PostgresConnector(f"sqlite:///{tmp_path / 'reclaim-validation.db'}")

    with pytest.raises(ValueError, match="reclaim_batch_size"):
        connector.execution_backend(reclaim_batch_size=0)


def test_strict_execution_source_rejects_unknown_fields(tmp_path) -> None:
    with pytest.raises(ValueError, match="unsupported fields"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "invalid-postgres-execution"},
                "resources": {
                    "db": {
                        "type": "postgres",
                        "dsn": f"sqlite:///{tmp_path / 'invalid.db'}",
                    },
                    "jobs": {
                        "type": "postgres_execution_source",
                        "connector": "db",
                        "namespace": "agent-api",
                        "task_names": ["run_agent"],
                        "unknown": True,
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_execution_source_requires_postgres_connector(tmp_path) -> None:
    with pytest.raises(TypeError, match="must be a PostgresConnector"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "invalid-postgres-dependency"},
                "resources": {
                    "queue": {"type": "memory", "maxsize": 1},
                    "jobs": {
                        "type": "postgres_execution_source",
                        "connector": "queue",
                        "namespace": "agent-api",
                        "task_names": ["run_agent"],
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))


def test_postgres_connector_error_does_not_leak_dsn_credentials() -> None:
    """DBAPI ``orig`` exceptions can echo the DSN; it must be scrubbed."""
    secret_dsn = "postgresql://reporter:pgpassword@db.internal:5432/appdb"
    import sqlalchemy as sa

    orig = sa.exc.OperationalError(
        statement="SELECT 1",
        params={},
        orig=Exception(
            f"connection to server at {secret_dsn} failed: "
            "FATAL: password authentication failed"
        ),
    )
    normalized = as_postgres_connector_operation_error(
        operation=ConnectorOperation.FETCH,
        exc=orig,
        source_name="postgres.incremental:users",
        secrets=[secret_dsn, "pgpassword"],
    )
    assert isinstance(normalized, ConnectorOperationError)
    assert "pgpassword" not in str(normalized.cause)
    assert "reporter:pgpassword" not in str(normalized.cause)
    assert "<redacted>" in str(normalized.cause)


def test_postgres_connector_secret_tokens_returns_independent_copy() -> None:
    secret = "connect-args-password"
    connector = PostgresConnector("sqlite://", connect_args={"password": secret})

    exposed = connector.secret_tokens()
    assert secret in exposed
    exposed.clear()

    assert secret in connector.secret_tokens()
    assert connector._secret_tokens() == connector.secret_tokens()
    asyncio.run(connector.close())


def test_postgres_connector_error_does_not_leak_connect_args_password() -> None:
    import sqlalchemy as sa

    secret = "connect-args-password"
    connector = PostgresConnector("sqlite://", connect_args={"password": secret})
    source = connector.incremental(table="users", key="id", cursor=("id",))

    def fail_fetch(limit: int) -> list[dict[str, Any]]:
        raise sa.exc.OperationalError(
            statement="SELECT 1",
            params={},
            orig=Exception(f"password {secret} was rejected"),
        )

    source._fetch_sync = fail_fetch

    with pytest.raises(ConnectorOperationError) as exc_info:
        asyncio.run(source.fetch(1))

    assert secret not in str(exc_info.value.cause)
    assert "<redacted>" in str(exc_info.value.cause)
    asyncio.run(connector.close())

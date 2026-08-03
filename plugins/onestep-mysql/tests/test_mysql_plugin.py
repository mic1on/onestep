from __future__ import annotations

import importlib
from importlib import metadata as importlib_metadata
from typing import Any

import pytest
from onestep_mysql import (
    BinlogSource,
    IncrementalTableSource,
    MySQLConnector,
    SQLAlchemyCursorStore,
    SQLAlchemyStateStore,
    TableSink,
    register,
)
from onestep_mysql.resilience import (
    MySQLErrorCause,
    as_mysql_connector_operation_error,
    classify_sqlalchemy_error,
)

from onestep.config import load_app_config
from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)
from onestep.resource_registry import ResourceRegistry


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "mysql"
        and entry_point.value == "onestep_mysql:register"
        for entry_point in entry_points
    )


def test_mysql_plugin_registers_catalog_metadata() -> None:
    registry = ResourceRegistry()
    register(registry)
    catalog = {entry.type: entry for entry in registry.catalog_entries()}
    connector_fields = {field.name: field for field in catalog["mysql"].fields}

    assert catalog["mysql"].roles == ("connector",)
    assert connector_fields["dsn"].required is True
    assert connector_fields["dsn"].secret is True
    assert connector_fields["password"].secret is True
    assert catalog["mysql_incremental"].roles == ("source",)
    assert catalog["mysql_incremental"].connector_types == ("mysql",)
    assert catalog["mysql_table_sink"].roles == ("sink",)
    assert catalog["mysql_table_sink"].connector_types == ("mysql",)


def test_sqlalchemy_state_store_is_not_exposed_by_core() -> None:
    import onestep

    assert not hasattr(onestep, "SQLAlchemyStateStore")
    assert not hasattr(onestep, "SQLAlchemyCursorStore")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("onestep.state_sqlalchemy")


def test_mysql_plugin_normalizes_sqlalchemy_errors() -> None:
    assert classify_sqlalchemy_error(TimeoutError("timeout")) is None

    from onestep_mysql.resilience import sa

    assert sa is not None
    sql_error = sa.exc.TimeoutError("timeout")
    assert classify_sqlalchemy_error(sql_error) is ConnectorErrorKind.TRANSIENT

    normalized = as_mysql_connector_operation_error(
        operation=ConnectorOperation.FETCH,
        exc=sql_error,
        source_name="mysql.incremental:users",
        retry_delay_s=2.0,
    )

    assert isinstance(normalized, ConnectorOperationError)
    assert normalized.backend == "mysql"
    assert normalized.operation is ConnectorOperation.FETCH
    assert normalized.kind is ConnectorErrorKind.TRANSIENT
    assert normalized.source_name == "mysql.incremental:users"
    assert normalized.retry_delay_s == 2.0
    assert isinstance(normalized.cause, MySQLErrorCause)
    assert "timeout" in str(normalized.cause)


def test_yaml_builds_mysql_resources_via_plugin_entry_point(tmp_path) -> None:
    dsn = f"sqlite:///{tmp_path / 'mysql-plugin.db'}"

    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {
                "name": "mysql-plugin",
                "state": "app_state",
            },
            "resources": {
                "db": {
                    "type": "mysql",
                    "dsn": dsn,
                },
                "app_state": {
                    "type": "mysql_state_store",
                    "connector": "db",
                },
                "cursor": {
                    "type": "mysql_cursor_store",
                    "connector": "db",
                },
                "users": {
                    "type": "mysql_incremental",
                    "connector": "db",
                    "table": "users",
                    "key": "id",
                    "cursor": ["updated_at", "id"],
                    "state": "cursor",
                },
                "changes": {
                    "type": "mysql_binlog",
                    "connector": "db",
                    "server_id": 18491,
                    "schemas": ["onestep"],
                    "tables": ["users"],
                    "events": ["insert", "update", "delete"],
                    "state": "cursor",
                    "state_key": "users-cdc",
                },
                "processed": {
                    "type": "mysql_table_sink",
                    "connector": "db",
                    "table": "processed_users",
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    assert isinstance(app.resources["db"], MySQLConnector)
    assert isinstance(app.resources["app_state"], SQLAlchemyStateStore)
    assert isinstance(app.resources["cursor"], SQLAlchemyCursorStore)
    assert isinstance(app.resources["users"], IncrementalTableSource)
    assert app.resources["users"].state is app.resources["cursor"]
    assert isinstance(app.resources["changes"], BinlogSource)
    assert app.resources["changes"].state is app.resources["cursor"]
    assert isinstance(app.resources["processed"], TableSink)
    assert app.state is app.resources["app_state"]


def test_yaml_builds_table_sink_with_update_control(tmp_path) -> None:
    dsn = f"sqlite:///{tmp_path / 'mysql-plugin.db'}"

    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {
                "name": "mysql-plugin",
            },
            "resources": {
                "db": {
                    "type": "mysql",
                    "dsn": dsn,
                },
                "processed": {
                    "type": "mysql_table_sink",
                    "connector": "db",
                    "table": "processed_users",
                    "mode": "upsert",
                    "keys": ["id"],
                    "update_columns": ["name", "email"],
                    "update_expr": {
                        "updated_at": "NOW(6)",
                    },
                    "serialize_json": "auto",
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    sink = app.resources["processed"]
    assert isinstance(sink, TableSink)
    assert sink.mode == "upsert"
    assert sink.keys == ("id",)
    assert sink.update_columns == ("name", "email")
    assert sink.update_expr == {"updated_at": "NOW(6)"}
    assert sink.serialize_json == "auto"


def test_yaml_empty_update_columns_is_distinct_from_unset(tmp_path) -> None:
    dsn = f"sqlite:///{tmp_path / 'mysql-plugin.db'}"

    app = load_app_config(
        {
            "apiVersion": "onestep/v1alpha1",
            "kind": "App",
            "app": {"name": "mysql-plugin"},
            "resources": {
                "db": {"type": "mysql", "dsn": dsn},
                "processed": {
                    "type": "mysql_table_sink",
                    "connector": "db",
                    "table": "processed_users",
                    "mode": "upsert",
                    "keys": ["id"],
                    "update_columns": [],
                    "update_expr": {"updated_at": "NOW(6)"},
                },
            },
            "tasks": [],
        },
        strict=True,
    )

    sink = app.resources["processed"]
    assert isinstance(sink, TableSink)
    assert sink.update_columns == ()
    assert sink.update_expr == {"updated_at": "NOW(6)"}


def test_yaml_rejects_empty_update_columns_without_update_expr(tmp_path) -> None:
    dsn = f"sqlite:///{tmp_path / 'mysql-plugin.db'}"

    with pytest.raises(ValueError, match="update_expr"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "mysql-plugin"},
                "resources": {
                    "db": {"type": "mysql", "dsn": dsn},
                    "processed": {
                        "type": "mysql_table_sink",
                        "connector": "db",
                        "table": "processed_users",
                        "mode": "upsert",
                        "keys": ["id"],
                        "update_columns": [],
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_yaml_rejects_update_expr_in_insert_mode(tmp_path) -> None:
    dsn = f"sqlite:///{tmp_path / 'mysql-plugin.db'}"

    with pytest.raises(ValueError, match="update_expr"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "mysql-plugin"},
                "resources": {
                    "db": {"type": "mysql", "dsn": dsn},
                    "processed": {
                        "type": "mysql_table_sink",
                        "connector": "db",
                        "table": "processed_users",
                        "mode": "insert",
                        "update_expr": {"updated_at": "NOW(6)"},
                    },
                },
                "tasks": [],
            },
            strict=True,
        )


def test_yaml_rejects_non_string_update_expr_values(tmp_path) -> None:
    dsn = f"sqlite:///{tmp_path / 'mysql-plugin.db'}"

    with pytest.raises(TypeError, match="update_expr"):
        load_app_config(
            {
                "apiVersion": "onestep/v1alpha1",
                "kind": "App",
                "app": {"name": "mysql-plugin"},
                "resources": {
                    "db": {"type": "mysql", "dsn": dsn},
                    "processed": {
                        "type": "mysql_table_sink",
                        "connector": "db",
                        "table": "processed_users",
                        "mode": "upsert",
                        "keys": ["id"],
                        "update_expr": {"updated_at": 123},
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


def test_mysql_connector_error_does_not_leak_dsn_credentials() -> None:
    """DBAPI ``orig`` exceptions can echo the DSN; it must be scrubbed."""
    import sqlalchemy as sa

    secret_dsn = "mysql://reporter:mysqlpass@db.internal:3306/appdb"
    orig = sa.exc.OperationalError(
        statement="SELECT 1",
        params={},
        orig=Exception(
            f"Access denied for user 'reporter'@'host' (using password: YES) "
            f"connecting to {secret_dsn}"
        ),
    )
    normalized = as_mysql_connector_operation_error(
        operation=ConnectorOperation.FETCH,
        exc=orig,
        source_name="mysql.incremental:users",
        secrets=[secret_dsn, "mysqlpass", "reporter:mysqlpass"],
    )
    assert isinstance(normalized, ConnectorOperationError)
    assert "mysqlpass" not in str(normalized.cause)
    assert "reporter:mysqlpass" not in str(normalized.cause)
    assert "<redacted>" in str(normalized.cause)


def test_mysql_connector_initializes_cache_and_collects_engine_option_secrets() -> None:
    connector = MySQLConnector(
        "sqlite://",
        connect_args={"password": "engine-option-secret"},
    )

    assert connector._tables == {}
    assert "engine-option-secret" in connector._secret_tokens()

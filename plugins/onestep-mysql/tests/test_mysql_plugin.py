from __future__ import annotations

import importlib
from importlib import metadata as importlib_metadata
from typing import Any

import pytest

from onestep.resilience import ConnectorErrorKind
from onestep.config import load_app_config
from onestep_mysql import (
    IncrementalTableSource,
    MySQLConnector,
    SQLAlchemyCursorStore,
    SQLAlchemyStateStore,
    TableSink,
)
from onestep_mysql.resilience import classify_sqlalchemy_error


def test_package_exposes_onestep_resource_entry_point() -> None:
    entry_points = _entry_points_for_group("onestep.resources")

    assert any(
        entry_point.name == "mysql"
        and entry_point.value == "onestep_mysql:register"
        for entry_point in entry_points
    )


def test_sqlalchemy_state_store_is_not_exposed_by_core() -> None:
    import onestep

    assert not hasattr(onestep, "SQLAlchemyStateStore")
    assert not hasattr(onestep, "SQLAlchemyCursorStore")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("onestep.state_sqlalchemy")


def test_mysql_plugin_registers_sqlalchemy_error_classifier() -> None:
    assert classify_sqlalchemy_error(TimeoutError("timeout")) is None

    from onestep_mysql.resilience import sa

    assert sa is not None
    assert classify_sqlalchemy_error(sa.exc.TimeoutError("timeout")) is ConnectorErrorKind.TRANSIENT


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
    assert isinstance(app.resources["processed"], TableSink)
    assert app.state is app.resources["app_state"]


def _entry_points_for_group(group: str) -> tuple[Any, ...]:
    entry_points = importlib_metadata.entry_points()
    if hasattr(entry_points, "select"):
        return tuple(entry_points.select(group=group))
    return tuple(entry_points.get(group, ()))

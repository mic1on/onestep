"""Phase 1 canonical-package contract tests for onestep-sql (issue #133).

These pin the public contract of the new canonical ``onestep-sql`` distribution
introduced in Phase 1 of the onestep-mysql / onestep-postgres consolidation:

* the single ``sql`` entry point registers exactly the same 14 YAML resource
  types as the two legacy plugins combined (verbatim-copy invariant);
* the ``[mysql]`` / ``postgres`` / ``all`` extras are declared;
* backend-specific capabilities stay backend-specific (mysql_binlog is MySQL
  only, postgres_execution_source is PostgreSQL only);
* the package imports without either database driver installed at import time
  (driver imports are lazy / inside functions).

No live database required. Phase 0 already proved the legacy plugins match the
golden baseline, so canonical == legacy transitively proves canonical == baseline.
"""

from __future__ import annotations

import importlib.metadata as md
import pathlib

import pytest

from onestep.resource_registry import ResourceRegistry
from onestep_mysql.resources import register_resources as register_mysql_legacy
from onestep_postgres.resources import register_resources as register_postgres_legacy
from onestep_sql import mysql as mysql_pkg
from onestep_sql import postgres as postgres_pkg
from onestep_sql import register_resources

EXPECTED_TYPES = {
    # MySQL
    "mysql", "mysql_state_store", "mysql_cursor_store", "mysql_table_queue",
    "mysql_incremental", "mysql_binlog", "mysql_table_sink",
    # PostgreSQL
    "postgres", "postgres_state_store", "postgres_cursor_store",
    "postgres_table_queue", "postgres_incremental", "postgres_execution_source",
    "postgres_table_sink",
}


def _normalize(value):
    if isinstance(value, dict):
        return {k: _normalize(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(i) for i in value]
    return value


def _catalog(registry: ResourceRegistry) -> dict[str, dict]:
    return _normalize({e.type: e.as_dict() for e in registry.catalog_entries()})


def _select_entry_points(group: str):
    eps = md.entry_points()
    if hasattr(eps, "select"):
        return eps.select(group=group)
    # Python 3.9: entry_points() returns a dict of group -> list[EntryPoint]
    if isinstance(eps, dict):
        return eps.get(group, [])
    return [ep for ep in eps if ep.group == group]


def test_register_resources_exposes_exactly_14_types():
    registry = ResourceRegistry()
    register_resources(registry)
    types = {e.type for e in registry.catalog_entries()}
    assert types == EXPECTED_TYPES
    assert len(types) == 14


def test_catalog_matches_legacy_combined():
    """Canonical must be byte-identical to both legacy plugins registered together."""
    legacy = ResourceRegistry()
    register_mysql_legacy(legacy)
    register_postgres_legacy(legacy)

    canonical = ResourceRegistry()
    register_resources(canonical)

    assert _catalog(canonical) == _catalog(legacy)


def test_sql_entry_point_resolves_and_is_callable():
    sql_ep = next(
        (ep for ep in _select_entry_points("onestep.resources") if ep.name == "sql"),
        None,
    )
    assert sql_ep is not None, "onestep-sql must declare the `sql` entry point"
    loaded = sql_ep.load()
    assert callable(loaded)
    registry = ResourceRegistry()
    loaded(registry)
    assert {e.type for e in registry.catalog_entries()} == EXPECTED_TYPES


def test_extras_declared():
    requires = md.requires("onestep-sql") or []
    joined = "\n".join(requires)
    for extra in ("mysql", "postgres", "all"):
        assert (
            f"extra == '{extra}'" in joined or f'extra == "{extra}"' in joined
        ), f"extra {extra!r} not declared"


def test_package_imports_without_drivers_at_import_time():
    # onestep_sql, .mysql and .postgres all import successfully here even if
    # asyncmy / psycopg are absent; drivers are only imported lazily at runtime.
    assert mysql_pkg.MySQLConnector is not None
    assert postgres_pkg.PostgresConnector is not None


def test_backend_specific_capabilities_stay_backend_specific():
    # MySQL-only capability must not leak into the postgres subpackage.
    assert hasattr(mysql_pkg, "BinlogSource")
    assert not hasattr(postgres_pkg, "BinlogSource")
    # PostgreSQL-only tracked-execution capability must not leak into mysql.
    assert hasattr(postgres_pkg, "PostgresExecutionSource")
    assert not hasattr(mysql_pkg, "PostgresExecutionSource")


def test_register_resources_is_idempotent_alias_on_subpackages():
    assert mysql_pkg.register_resources is not None
    assert postgres_pkg.register_resources is not None
    r1 = ResourceRegistry()
    register_resources(r1)
    r2 = ResourceRegistry()
    mysql_pkg.register_resources(r2)
    postgres_pkg.register_resources(r2)
    assert _catalog(r1) == _catalog(r2)

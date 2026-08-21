"""Phase 0 baseline + contract tests for the onestep SQL plugins.

These tests pin the *current* public contract of ``onestep-mysql`` and
``onestep-postgres`` so that the later consolidation into a single
``onestep-sql`` distribution (tracking issue #133, design in
``docs/superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md``)
cannot silently change:

* the 14 YAML resource type names (7 MySQL + 7 PostgreSQL),
* their catalog roles, fields, defaults, options and connector boundaries,
* the public Python API surface exported from each package, and
* the historical submodule import paths that compatibility forwarding must keep.

They register handlers directly (no entry-point discovery) for
determinism. The full install-permutation / duplicate-registration matrix
is covered later in Phase 3 (``scripts/run-integration-tests.sh`` + CI),
because it requires multiple installed wheels.

No live database is required: every assertion here is structural.
"""
from __future__ import annotations

import importlib
import json
from pathlib import Path

from onestep.resource_registry import ResourceRegistry

from onestep_mysql.resources import register_resources as register_mysql
from onestep_postgres.resources import register_resources as register_postgres

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "sql_resource_catalog.json"

EXPECTED_TYPES = frozenset(
    {
        # MySQL (7)
        "mysql",
        "mysql_state_store",
        "mysql_cursor_store",
        "mysql_table_queue",
        "mysql_incremental",
        "mysql_binlog",
        "mysql_table_sink",
        # PostgreSQL (7)
        "postgres",
        "postgres_state_store",
        "postgres_cursor_store",
        "postgres_table_queue",
        "postgres_incremental",
        "postgres_execution_source",
        "postgres_table_sink",
    }
)

# Public API that compatibility forwarding (Phase 3) must preserve by identity.
MYSQL_REQUIRED_PUBLIC = {
    "MySQLConnector",
    "TableSink",
    "BinlogSource",
    "SQLAlchemyStateStore",
    "SQLAlchemyCursorStore",
    "IncrementalTableSource",
    "TableQueueSource",
    "IncrementalDelivery",
    "TableQueueDelivery",
    "BinlogDelivery",
    "classify_sqlalchemy_error",
    "register",
    "register_resources",
}

POSTGRES_REQUIRED_PUBLIC = {
    "PostgresConnector",
    "PostgresTableSink",
    "PostgresExecutionBackend",
    "PostgresExecutionSource",
    "PostgresExecutionDelivery",
    "PostgresIncrementalSource",
    "PostgresTableQueueSource",
    "PostgresTableQueueDelivery",
    "ExecutionLease",
    "HeartbeatResult",
    "StaleExecutionLease",
    "SQLAlchemyStateStore",
    "SQLAlchemyCursorStore",
    "IncrementalDelivery",
    "classify_sqlalchemy_error",
    "register",
    "register_resources",
}

# Historical submodule import paths that must keep resolving (P0.2).
MYSQL_SUBMODULES = ["connector", "resources", "resilience", "state_sqlalchemy"]
POSTGRES_SUBMODULES = [
    "connector",
    "resources",
    "resilience",
    "state_sqlalchemy",
    "execution_backend",
    "execution_schema",
    "execution_source",
]


def _normalize(value):
    # JSON has no tuple type; normalize tuples to lists so the golden snapshot
    # (loaded from JSON) compares equal to the runtime catalog (which may carry
    # tuples for `default`/`options`). YAML consumers do not distinguish them.
    if isinstance(value, dict):
        return {key: _normalize(val) for key, val in value.items()}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    return value


def _catalog(registry: ResourceRegistry) -> dict[str, dict]:
    return _normalize({entry.type: entry.as_dict() for entry in registry.catalog_entries()})


def test_fourteen_types_registered_once() -> None:
    registry = ResourceRegistry()
    register_mysql(registry)
    register_postgres(registry)
    types = set(registry.handlers().keys())
    assert types == EXPECTED_TYPES
    assert len(types) == 14


def test_catalog_matches_baseline_snapshot() -> None:
    registry = ResourceRegistry()
    register_mysql(registry)
    register_postgres(registry)
    actual = _catalog(registry)
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    assert actual == expected


def test_idempotent_reregistration_does_not_duplicate() -> None:
    # ResourceRegistry tolerates fully-equal duplicate handlers; re-registering
    # both plugins must not raise and must not create duplicate types.
    registry = ResourceRegistry()
    register_mysql(registry)
    register_postgres(registry)
    register_mysql(registry)
    register_postgres(registry)
    assert set(registry.handlers().keys()) == EXPECTED_TYPES
    assert len(registry.handlers()) == 14


def test_mysql_public_api_surface() -> None:
    import onestep_mysql

    missing = MYSQL_REQUIRED_PUBLIC - set(dir(onestep_mysql))
    assert not missing, f"onestep_mysql missing public symbols: {sorted(missing)}"
    assert onestep_mysql.register is onestep_mysql.register_resources


def test_postgres_public_api_surface() -> None:
    import onestep_postgres

    missing = POSTGRES_REQUIRED_PUBLIC - set(dir(onestep_postgres))
    assert not missing, f"onestep_postgres missing public symbols: {sorted(missing)}"
    assert onestep_postgres.register is onestep_postgres.register_resources


def test_mysql_submodule_import_paths() -> None:
    for submodule in MYSQL_SUBMODULES:
        module = importlib.import_module(f"onestep_mysql.{submodule}")
        assert module is not None


def test_postgres_submodule_import_paths() -> None:
    for submodule in POSTGRES_SUBMODULES:
        module = importlib.import_module(f"onestep_postgres.{submodule}")
        assert module is not None

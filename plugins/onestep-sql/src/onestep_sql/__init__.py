from __future__ import annotations

from onestep.resource_registry import ResourceRegistry

from . import mysql, postgres, sqlite
from .mysql import register_resources as _register_mysql
from .postgres import register_resources as _register_postgres
from .sqlite import register_resources as _register_sqlite

__all__ = ["register_resources", "mysql", "postgres", "sqlite"]


def register_resources(registry: ResourceRegistry) -> None:
    """Register every onestep-sql resource type (MySQL + PostgreSQL + SQLite).

    This is the single entry point for the canonical ``onestep-sql``
    distribution (declared as ``sql`` in the ``onestep.resources`` group).
    It delegates to the per-backend registrars without changing any of the
    existing YAML type names, their catalog roles, or their fields.
    """
    _register_mysql(registry)
    _register_postgres(registry)
    _register_sqlite(registry)

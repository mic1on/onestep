"""Shared table-sink update-policy machinery for the onestep-sql backends.

Phase 2 of the mysql/postgres consolidation (issue #133, design §3.1) keeps
exactly one copy of the update-policy helpers that previously lived in
parallel in both backend ``connector.py`` modules (see the retired
``scripts/check_plugin_drift.py``):

* ``_UPDATE_COLUMN_POLICIES`` / ``_normalize_update_columns`` — validation of
  the ``update_columns`` write policies (``overwrite`` / ``skip_null`` /
  ``backfill``) against keys and ``update_expr``;
* :class:`TableSinkUpdatePolicy` — the ``_update_payload`` column-write
  policy and ``_coerce_json_values`` JSON serialization mixin used by both
  ``TableSink`` (mysql) and ``PostgresTableSink``;
* the upsert key preflight (:func:`validate_upsert_key_uniqueness`) that keeps
  ``mode: upsert`` from silently degrading into plain inserts (issue #188).

Everything that genuinely differs between the backends stays in their
``connector.py``: constructor validation order, ``_build_statement`` SQL
dialect branches (``ON DUPLICATE KEY UPDATE`` vs ``ON CONFLICT DO UPDATE``)
and the ``_send`` logging.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar

from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)

try:
    import sqlalchemy as sa
except ImportError:  # pragma: no cover - exercised when optional deps are missing
    sa = None

_UPDATE_COLUMN_POLICIES = frozenset({"overwrite", "skip_null", "backfill"})


def _normalize_update_columns(
    update_columns: Sequence[str | Mapping[str, str]] | None,
    *,
    keys: tuple[str, ...],
    update_expr: Mapping[str, str] | None = None,
) -> tuple[tuple[str, ...] | None, dict[str, str]]:
    if update_columns is None:
        return None, {}
    names: list[str] = []
    policies: dict[str, str] = {}
    for entry in update_columns:
        if isinstance(entry, str):
            if not entry:
                raise ValueError("update_columns entries must be non-empty")
            name, policy = entry, "overwrite"
        elif isinstance(entry, Mapping):
            unknown_keys = set(entry) - {"name", "policy"}
            if unknown_keys:
                raise ValueError(f"unknown update_columns entry keys: {', '.join(sorted(unknown_keys))}")
            name = entry.get("name")
            policy = entry.get("policy", "overwrite")
            if not isinstance(name, str) or not name:
                raise ValueError("update_columns entry requires a non-empty 'name'")
            if policy not in _UPDATE_COLUMN_POLICIES:
                raise ValueError(
                    "update_columns policy must be one of 'overwrite', 'skip_null' or 'backfill', "
                    f"got {policy!r}"
                )
            if name in keys:
                raise ValueError(f"update_columns policy cannot apply to key column {name!r}")
        else:
            raise TypeError("update_columns entries must be strings or mappings")
        if name in policies:
            raise ValueError(f"duplicate update column {name!r}")
        names.append(name)
        policies[name] = policy
    update_expr_keys = set(update_expr) if update_expr else set()
    conflicting = sorted(set(policies) & update_expr_keys)
    if conflicting:
        raise ValueError(f"update_columns policy conflicts with update_expr for: {', '.join(conflicting)}")
    return tuple(names), policies


def _unique_column_sets(table: sa.Table) -> list[tuple[str, tuple[str, ...]]]:
    """Collect every uniqueness guarantee as ``(label, column_names)``.

    SQLAlchemy reflects uniqueness in **two** places and the split is
    dialect-dependent (verified against MySQL 8.0, PostgreSQL 16 and SQLite):

    * ``CREATE UNIQUE INDEX`` -> ``table.indexes`` with ``unique=True`` on
      every dialect;
    * inline ``UNIQUE`` / ``CONSTRAINT ... UNIQUE`` -> ``table.indexes`` on
      MySQL, but a ``UniqueConstraint`` in ``table.constraints`` on PostgreSQL
      and SQLite.

    Reading only ``table.indexes`` (as the original issue proposed) would
    therefore silently miss declared unique constraints on PostgreSQL and
    SQLite. Primary keys are included because a matching PK is an equally
    valid conflict target.
    """
    found: list[tuple[str, tuple[str, ...]]] = []
    seen: set[frozenset[str]] = set()

    def add(label: str, columns: Any) -> None:
        names = tuple(column.name for column in columns)
        if not names:
            return
        marker = frozenset(names)
        if marker in seen:
            return
        seen.add(marker)
        found.append((label, names))

    primary_key = getattr(table, "primary_key", None)
    if primary_key is not None:
        add("PRIMARY KEY", list(primary_key.columns))

    for index in getattr(table, "indexes", ()) or ():
        if getattr(index, "unique", False):
            add(f"unique index {index.name!r}", list(index.columns))

    for constraint in getattr(table, "constraints", ()) or ():
        if sa is not None and isinstance(constraint, sa.UniqueConstraint):
            add(f"unique constraint {constraint.name!r}", list(constraint.columns))

    return found


def _describe_existing_indexes(table: sa.Table) -> str:
    """Render every reflected index, so a near-miss is visible in the error.

    The ceegic-sync failure mode was a *plain* index on the declared key: the
    operator needs to see that ``idx_device_key`` exists yet is not unique,
    not merely that no uniqueness was found.
    """
    entries: list[str] = []
    primary_key = getattr(table, "primary_key", None)
    if primary_key is not None and list(primary_key.columns):
        columns = ", ".join(column.name for column in primary_key.columns)
        entries.append(f"PRIMARY KEY ({columns})")
    for constraint in getattr(table, "constraints", ()) or ():
        if sa is not None and isinstance(constraint, sa.UniqueConstraint):
            columns = ", ".join(column.name for column in constraint.columns)
            entries.append(f"UNIQUE constraint {constraint.name!r} ({columns})")
    for index in getattr(table, "indexes", ()) or ():
        columns = ", ".join(column.name for column in index.columns)
        kind = "UNIQUE index" if getattr(index, "unique", False) else "index"
        entries.append(f"{kind} {index.name!r} ({columns})")
    return "; ".join(entries) if entries else "none"


def validate_upsert_key_uniqueness(
    *,
    table: sa.Table,
    keys: Sequence[str],
    backend: str,
    sink_name: str | None = None,
) -> None:
    """Fail loudly when ``mode: upsert`` has no usable conflict target.

    ``mysql_table_sink`` renders ``INSERT ... ON DUPLICATE KEY UPDATE``, which
    declares no conflict target and only takes the update branch when *some*
    unique key is hit. With a non-unique index on ``keys`` every run inserts
    fresh duplicates and nothing reports an error. PostgreSQL and SQLite do
    raise (their ``ON CONFLICT`` declares the target), but only per row and
    with a dialect-specific message; this preflight makes all three backends
    fail identically, before the first write.

    The rule is **exact set equality** between ``keys`` and a unique column
    set (order-insensitive, composite included). A subset is deliberately not
    accepted: a unique index on ``(a, b)`` does not constrain ``a`` alone.
    """
    key_set = frozenset(keys)
    candidates = _unique_column_sets(table)
    if any(frozenset(columns) == key_set for _, columns in candidates):
        return

    declared = ", ".join(repr(key) for key in keys)
    raise ConnectorOperationError(
        backend=backend,
        operation=ConnectorOperation.SEND,
        kind=ConnectorErrorKind.MISCONFIGURED,
        source_name=sink_name,
        message=(
            f"{backend} table sink {sink_name or table.name} has mode 'upsert' with keys "
            f"({declared}) but table {table.name!r} has no unique constraint or primary key "
            f"whose columns exactly match those keys, so writes would silently insert "
            f"duplicates instead of updating. Existing indexes: {_describe_existing_indexes(table)}. "
            f"Add a UNIQUE index covering exactly ({declared}) (or fix the declared keys)."
        ),
    )


class TableSinkUpdatePolicy:
    """Mixin implementing the shared SQL table-sink column-write policy.

    Host sinks must set these instance attributes (both existing backends
    already do): ``keys``, ``update_columns``, ``column_policies``,
    ``update_expr`` and ``serialize_json``.
    """

    #: Backend label used in the preflight error message (``mysql`` etc.).
    _backend: ClassVar[str] = "sql"

    def _validate_upsert_keys(self, table: sa.Table) -> None:
        """Preflight ``mode: upsert`` against the reflected table metadata.

        Cached on the sink because a sink writes the same table for its whole
        lifetime: the reflection-derived answer cannot change underneath a
        running sink, so the per-row path pays one attribute check after the
        first write (issue #188 acceptance: no per-row reflection overhead).
        """
        if self.mode != "upsert":
            return
        if getattr(self, "_upsert_keys_validated", False):
            return
        validate_upsert_key_uniqueness(
            table=table, keys=self.keys, backend=self._backend, sink_name=self.name
        )
        self._upsert_keys_validated = True

    def _update_payload(self, payload: dict[str, Any], table: sa.Table) -> tuple[dict[str, Any], bool]:
        if self.update_columns is not None:
            candidates = {key: value for key, value in payload.items() if key in self.update_columns}
        else:
            candidates = {key: value for key, value in payload.items() if key not in self.keys}
        update_payload: dict[str, Any] = {}
        skipped = False
        for column, value in candidates.items():
            policy = self.column_policies.get(column, "overwrite")
            if policy == "skip_null" and value is None:
                skipped = True
                continue
            if policy == "backfill":
                update_payload[column] = sa.func.coalesce(table.columns[column], value)
            else:
                update_payload[column] = value
        for column, expr in self.update_expr.items():
            update_payload[column] = sa.literal_column(expr)
        return update_payload, skipped

    def _coerce_json_values(self, payload: dict[str, Any], table: sa.Table) -> dict[str, Any]:
        if self.serialize_json == "never":
            return payload
        coerced = dict(payload)
        for column_name, value in list(payload.items()):
            if not isinstance(value, (list, dict)):
                continue
            column = table.columns.get(column_name)
            if column is None:
                continue
            is_json_column = isinstance(column.type, sa.JSON)
            if self.serialize_json == "always" or not is_json_column:
                coerced[column_name] = json.dumps(value, ensure_ascii=False)
        return coerced

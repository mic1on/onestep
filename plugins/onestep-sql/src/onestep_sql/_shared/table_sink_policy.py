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
* the batch-payload machinery (issue #189): list bodies share one
  ``_prepare_batch_rows`` validation/normalization pass, one ``_send_batch``
  execution flow (single transaction) and one chunking policy, while each
  backend only contributes its dialect-specific ``_build_batch_statements``.
* the upsert key preflight (:func:`validate_upsert_key_uniqueness`) that keeps
  ``mode: upsert`` from silently degrading into plain inserts (issue #188).

Everything that genuinely differs between the backends stays in their
``connector.py``: constructor validation order, ``_build_statement`` SQL
dialect branches (``ON DUPLICATE KEY UPDATE`` vs ``ON CONFLICT DO UPDATE``)
and the ``_send`` logging.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Iterator

from onestep.resilience import (
    ConnectorErrorKind,
    ConnectorOperation,
    ConnectorOperationError,
)

logger = logging.getLogger(__name__)

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

    # ------------------------------------------------------------------
    # Batch payloads (issue #189).
    #
    # ``send()`` accepts a list/tuple of row mappings in addition to the
    # historical single mapping. The red-team review of the feasibility
    # study (docs/superpowers/specs/2026-09-20-issue-189-*) fixed these
    # semantics, all measured on real MySQL 8 / PostgreSQL 16 / SQLite:
    #
    # * one ``send()`` = one transaction = all-or-nothing (chunks included);
    # * payload-shape violations raise PERMANENT ConnectorOperationError
    #   *before* the transaction opens — never half-written batches;
    # * every row must be a mapping and all rows must share one column set
    #   (SQLAlchemy renders multi-row VALUES from the first row and would
    #   silently drop extra columns of later rows);
    # * same-batch duplicate upsert keys are rejected: MySQL/SQLite would
    #   silently apply last-wins while PostgreSQL raises
    #   ``cannot affect row a second time``;
    # * ``skip_null`` becomes a shared ``CASE WHEN <ref> IS NULL THEN col
    #   ELSE <ref> END`` so NULL payloads keep existing values per row;
    #   rows whose update columns are *all* filtered are removed from the
    #   batch entirely, mirroring the single-row "skip the write" path;
    # * ``update`` mode uses bindparam executemany with names that can
    #   never collide with column names, and parameter dictionaries are
    #   projected explicitly so stray same-named keys cannot leak into SET
    #   (the B2 injection-shaped defect);
    # * ``update_columns`` is intersected with the payload columns.
    # ------------------------------------------------------------------

    def _batch_payload_error(self, message: str) -> ConnectorOperationError:
        return ConnectorOperationError(
            backend=self._backend,
            operation=ConnectorOperation.SEND,
            kind=ConnectorErrorKind.PERMANENT,
            source_name=self.name,
            message=message,
        )

    def _batch_candidate_columns(
        self, columns: frozenset[str]
    ) -> tuple[str, ...]:
        """Update candidates as ``update_columns`` ∩ payload (§2.2.1)."""
        if self.update_columns is not None:
            return tuple(column for column in self.update_columns if column in columns)
        return tuple(column for column in sorted(columns) if column not in self.keys)

    def _prepare_batch_rows(
        self, rows: Sequence[Mapping[str, Any]], table: sa.Table
    ) -> tuple[list[dict[str, Any]], int, tuple[str, ...]]:
        """Validate and normalize a list payload before any write.

        Returns ``(prepared_rows, skipped_row_count, candidate_columns)``.
        Every shape violation raises before the transaction opens.
        """
        for index, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise self._batch_payload_error(
                    f"batch item {index} must be a mapping, got {type(row).__name__}"
                )
        prepared = [dict(row) for row in rows]

        first_columns = frozenset(prepared[0])
        for index, row in enumerate(prepared[1:], start=1):
            row_columns = frozenset(row)
            if row_columns != first_columns:
                missing = sorted(first_columns - row_columns)
                extra = sorted(row_columns - first_columns)
                raise self._batch_payload_error(
                    f"batch rows must share one column set: row {index} differs from row 0 "
                    f"(missing {missing}, unexpected {extra})"
                )

        prepared = [self._coerce_json_values(row, table) for row in prepared]

        candidates = self._batch_candidate_columns(first_columns)
        if self.mode in {"upsert", "update"}:
            if not self.keys:
                raise self._batch_payload_error(f"{self.mode} mode requires keys")
            missing_keys = [key for key in self.keys if key not in first_columns]
            if missing_keys:
                raise self._batch_payload_error(
                    f"{self.mode} mode requires keys present in payload: {', '.join(missing_keys)}"
                )
            if not self.update_expr and not candidates:
                raise self._batch_payload_error(
                    f"{self.mode} mode requires at least one update column or update_expr"
                )

        if self.mode == "upsert":
            seen_keys: set[tuple[Any, ...]] = set()
            for index, row in enumerate(prepared):
                key_value = tuple(row[key] for key in self.keys)
                try:
                    duplicate = key_value in seen_keys
                except TypeError:
                    raise self._batch_payload_error(
                        f"batch item {index} has an unhashable key value {key_value!r}"
                    ) from None
                if duplicate:
                    raise self._batch_payload_error(
                        f"batch item {index} duplicates earlier keys {key_value!r}; "
                        "same-statement upserts diverge across dialects "
                        "(MySQL/SQLite apply last-wins, PostgreSQL fails). "
                        "Deduplicate upstream or split the batch."
                    )
                seen_keys.add(key_value)

        skipped = 0
        if self.mode in {"upsert", "update"} and not self.update_expr:
            kept: list[dict[str, Any]] = []
            for row in prepared:
                fully_filtered = all(
                    row[column] is None
                    and self.column_policies.get(column, "overwrite") == "skip_null"
                    for column in candidates
                )
                if fully_filtered:
                    skipped += 1
                else:
                    kept.append(row)
            prepared = kept
        return prepared, skipped, candidates

    def _upsert_batch_set(
        self,
        table: sa.Table,
        row_ref: Any,
        candidates: Sequence[str],
    ) -> dict[str, Any]:
        """Shared upsert SET clause keyed on per-row inserted/excluded refs.

        ``row_ref`` is ``stmt.inserted`` (MySQL) or ``stmt.excluded``
        (PostgreSQL/SQLite) taken from the *same* statement instance the
        ``on_duplicate_key_update``/``on_conflict_do_update`` is applied to.
        """
        set_: dict[str, Any] = {}
        for column in candidates:
            policy = self.column_policies.get(column, "overwrite")
            target = table.columns[column]
            new_value = row_ref[column]
            if policy == "skip_null":
                set_[column] = sa.case((new_value.is_(None), target), else_=new_value)
            elif policy == "backfill":
                set_[column] = sa.func.coalesce(target, new_value)
            else:
                set_[column] = new_value
        for column, expr in self.update_expr.items():
            set_[column] = sa.literal_column(expr)
        return set_

    @staticmethod
    def _batch_bind_names(columns: Sequence[str], prefix: str) -> dict[str, str]:
        """Map column → unique bindparam name that can never equal a column name.

        Bindparam names matching a column of the same statement are reserved
        for automatic VALUES/SET usage (CompileError) and raw row dicts would
        leak extra same-named keys into SET, so batch update remaps every
        column onto a prefixed, collision-checked name.
        """
        used = set(columns)
        names: dict[str, str] = {}
        stem = prefix
        while any(f"{stem}_{index}" in used for index in range(len(columns))):
            stem = f"_{stem}"
        for index, column in enumerate(columns):
            names[column] = f"{stem}_{index}"
        return names

    def _update_batch_statement(
        self, table: sa.Table, candidates: Sequence[str]
    ) -> tuple[Any, dict[str, Any]]:
        """Build the executemany UPDATE statement plus its row projector.

        Returns ``(statement, parameter_template)`` where the template maps
        bindparam name → source column for explicit per-row projection.
        """
        key_names = self._batch_bind_names(self.keys, "_onestep_key")
        column_names = self._batch_bind_names(candidates, "_onestep_value")
        conditions = [
            table.columns[key] == sa.bindparam(key_names[key]) for key in self.keys
        ]
        values: dict[str, Any] = {}
        for column in candidates:
            policy = self.column_policies.get(column, "overwrite")
            target = table.columns[column]
            new_value = sa.bindparam(column_names[column])
            if policy == "skip_null":
                values[column] = sa.case((new_value.is_(None), target), else_=new_value)
            elif policy == "backfill":
                values[column] = sa.func.coalesce(target, new_value)
            else:
                values[column] = new_value
        for column, expr in self.update_expr.items():
            values[column] = sa.literal_column(expr)
        statement = sa.update(table).where(sa.and_(*conditions)).values(**values)
        # bindparam name -> source column, for explicit per-row projection.
        parameter_template = {
            name: column
            for column, name in {**key_names, **column_names}.items()
        }
        return statement, parameter_template

    def _batch_row_limit(self, column_count: int) -> int | None:
        """Hard per-statement row ceiling for the engine's dialect, ``None`` = unlimited.

        Multi-row VALUES renders one bind parameter per row × column. SQLite's
        portable ``SQLITE_MAX_VARIABLE_NUMBER`` bound is 999, so sqlite engines
        clamp the chunk size; MySQL and PostgreSQL engines are unlimited (the
        former measured past 250k parameters on the text protocol, the latter
        runs batches through executemany pipelines whose per-statement
        parameter count equals the column count).
        """
        sync_engine = getattr(self.connector, "engine", None)
        if sync_engine is None:
            return None
        dialect = getattr(sync_engine, "sync_engine", sync_engine).dialect.name
        if dialect == "sqlite":
            return max(1, 999 // max(1, column_count))
        return None

    def _batch_chunks(
        self, rows: Sequence[Mapping[str, Any]]
    ) -> Iterator[list[Mapping[str, Any]]]:
        size = self.batch_size
        row_limit = self._batch_row_limit(max(1, len(rows[0])))
        if row_limit is not None:
            size = min(size, row_limit)
        size = max(1, size)
        for start in range(0, len(rows), size):
            yield list(rows[start : start + size])

    def _build_batch_statements(
        self,
        rows: Sequence[Mapping[str, Any]],
        table: sa.Table,
        candidates: Sequence[str],
    ) -> list[tuple[Any, list[dict[str, Any]] | None]]:
        """Backend hook: compile (statement, executemany params or None) pairs."""
        raise NotImplementedError

    async def _send_batch(self, rows: Sequence[Mapping[str, Any]]) -> None:
        """Shared batch execution: validate → build → one transaction."""
        if not rows:
            logger.debug("%s batch send skipped: empty payload", self.name)
            return
        table = await self.connector._table(self.table_name)
        self._validate_upsert_keys(table)
        prepared, skipped, candidates = self._prepare_batch_rows(rows, table)
        if skipped:
            logger.info(
                "%s batch skipped %d row(s): all update columns are null under skip_null policy",
                self.name,
                skipped,
            )
        if not prepared:
            return
        statements = self._build_batch_statements(prepared, table, candidates)
        matched_rows = 0
        async with self.connector.engine.begin() as conn:
            for statement, parameters in statements:
                if parameters is None:
                    result = await conn.execute(statement)
                else:
                    result = await conn.execute(statement, parameters)
                matched_rows += result.rowcount or 0
        if self.mode == "update" and matched_rows == 0:
            logger.info(
                "%s batch update matched no rows or values unchanged (%d row(s))",
                self.name,
                len(prepared),
            )

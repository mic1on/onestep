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
  ``TableSink`` (mysql) and ``PostgresTableSink``.

Everything that genuinely differs between the backends stays in their
``connector.py``: constructor validation order, ``_build_statement`` SQL
dialect branches (``ON DUPLICATE KEY UPDATE`` vs ``ON CONFLICT DO UPDATE``)
and the ``_send`` logging.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

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


class TableSinkUpdatePolicy:
    """Mixin implementing the shared SQL table-sink column-write policy.

    Host sinks must set these instance attributes (both existing backends
    already do): ``keys``, ``update_columns``, ``column_policies``,
    ``update_expr`` and ``serialize_json``.
    """

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

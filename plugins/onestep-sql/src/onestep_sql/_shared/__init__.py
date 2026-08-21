"""Internal shared SQL behaviour for the canonical ``onestep-sql`` package.

Phase 2 of the ``onestep-mysql`` / ``onestep-postgres`` consolidation
(issue #133, design in PR #134) keeps exactly one copy of the machinery that
previously lived in parallel in both backend subpackages (as monitored by the
now-retired ``scripts/check_plugin_drift.py``):

* :mod:`onestep_sql._shared.state_sqlalchemy` — the SQLAlchemy state/cursor
  stores; backends subclass them and override only the asyncio driver mapping
  (``mysql+asyncmy`` vs ``postgresql+psycopg``) and install hint;
* :mod:`onestep_sql._shared.table_sink_policy` — the table-sink
  ``update_columns`` write policies (``overwrite`` / ``skip_null`` /
  ``backfill``), the ``_normalize_update_columns`` validator and the
  ``_update_payload`` / ``_coerce_json_values`` mixin;
* :mod:`onestep_sql._shared.state_keys` — the default incremental state-key
  derivation feeding the at-least-once cursor state;
* :mod:`onestep_sql._shared.resilience` — the secret-token collection, message
  redaction, redacted error-cause base class and the
  ``ConnectorOperationError`` factory; the dialect-specific SQLAlchemy error
  classification tables stay in each backend's ``resilience.py``.

This package is **not** public API. Backend capabilities never move here:
MySQL binlog CDC stays in ``onestep_sql.mysql`` and the PostgreSQL tracked
execution modules stay in ``onestep_sql.postgres``. Dual-backend contract
tests live in ``tests/contract/test_onestep_sql_shared.py``.
"""

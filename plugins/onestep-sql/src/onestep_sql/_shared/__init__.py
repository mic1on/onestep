"""Internal shared SQL behaviour for the canonical ``onestep-sql`` package.

This package is a **placeholder** introduced in Phase 1 of the
``onestep-mysql`` / ``onestep-postgres`` consolidation (issue #133, design in
PR #134). Phase 1 only copies both backends verbatim under
``onestep_sql.mysql`` / ``onestep_sql.postgres``; no de-duplication happens yet.

Phase 2 will relocate the behaviours that ``scripts/check_plugin_drift.py``
currently monitors as duplicate pairs into this package:

* SQLAlchemy state stores (cursor / state)
* table-sink write policy
* incremental state-key sequencing
* shared contract / live test helpers

Until then the backend subpackages keep their own copies, and this module only
exists so the import path ``onestep_sql._shared`` is reserved and importable.
"""

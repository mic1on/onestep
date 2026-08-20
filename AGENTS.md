# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

## onestep-sql consolidation (issue #133)

- Authoritative design: `docs/superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md` + execution tasks doc beside it. Phase 2 (shared extraction) is merged; Phase 3 (forwarding distributions, root extras, worker, CI merge) is next.
- Shared SQL behaviour lives once in `plugins/onestep-sql/src/onestep_sql/_shared/` (`state_sqlalchemy`, `table_sink_policy`, `state_keys`, `resilience`). Backend adapters keep only driver mapping, install hints, dialect error-classification tables, `__init__` validation order, `_build_statement` SQL dialect branches, binlog (mysql) and tracked execution (postgres).
- `scripts/check_plugin_drift.py` and its CI job are retired; the replacement guardrail is `tests/contract/test_onestep_sql_shared.py` (dual-backend identity + behaviour proofs). Extend it when moving another parallel pair into `_shared`.
- Plugin test dirs share basenames (e.g. `test_state_sqlalchemy.py`), so run each plugin suite in its own pytest process — see `scripts/run-reliability-checks.sh`.
- Legacy `onestep-mysql`/`onestep-postgres` are `sys.modules` forwarders onto `onestep_sql.*`; keep object identity when refactoring canonical modules.

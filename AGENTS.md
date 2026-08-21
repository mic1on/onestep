# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

## onestep-sql consolidation (issue #133)

- Authoritative design: `docs/superpowers/specs/2026-08-20-onestep-sql-consolidation-design.md` + execution tasks doc beside it. Phases 0–3 are merged; Phase 4 (docs adoption) and Phase 5 (deprecation closeout) remain.
- `onestep-sql` is the canonical distribution for MySQL **and** PostgreSQL. Root extras `mysql`/`postgres`/`sql`/`all`/`dev`/`integration` resolve through `onestep-sql[mysql,postgres]`. New code: `pip install 'onestep-sql[mysql]'` and import from `onestep_sql.mysql` / `onestep_sql.postgres`.
- Legacy `onestep-mysql` (0.7.0) / `onestep-postgres` (0.6.0) are thin forwarding shims: no `onestep.resources` entry point, depend on `onestep-sql[mysql,sqlite]` / `onestep-sql[postgres,sqlite]`. The single `sql` entry point on `onestep-sql` registers all 14 YAML types, so new+old install permutations never double-register. `from onestep_mysql import ...` / `from onestep_postgres import ...` keep working via `sys.modules` forwarders with object identity.
- Shared SQL behaviour lives once in `plugins/onestep-sql/src/onestep_sql/_shared/` (`state_sqlalchemy`, `table_sink_policy`, `state_keys`, `resilience`). Backend adapters keep only driver mapping, install hints, dialect error-classification tables, `__init__` validation order, `_build_statement` SQL dialect branches, binlog (mysql) and tracked execution (postgres).
- `scripts/check_plugin_drift.py` and its CI job are retired; the replacement guardrail is `tests/contract/test_onestep_sql_shared.py` (dual-backend identity + behaviour proofs). Extend it when moving another parallel pair into `_shared`.
- `tests/contract/test_official_connector_conformance.py` derives the official connector set from plugins that still declare `[project.entry-points."onestep.resources"]`; the `sql` profile owns MySQL+PostgreSQL conformance since the shims dropped their entry points.
- Plugin test dirs share basenames (e.g. `test_state_sqlalchemy.py`), so run each plugin suite in its own pytest process — see `scripts/run-reliability-checks.sh`.
- Migration guide: `docs/guide/migrate-to-onestep-sql.md`.

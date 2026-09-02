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

## OneStepApp lifecycle decomposition (issue #146)

- Design: `docs/superpowers/specs/2026-08-27-onestep-app-lifecycle-decomposition-design.md`.
- `src/onestep/app.py` `OneStepApp` is a thin facade (~390 lines): construction, task/resource registration (`bind_resources`/`register_resource`/`task`/`set_reporter_summary`), `describe`/`load`/`run`, module-level `_describe_resource`/`_invoke_app_factory`. Everything else delegates.
- `src/onestep/runtime/lifecycle.py` `LifecycleController(app)` owns the asyncio state: shutdown/drain/pause signals + waiters, runner registry + per-task `asyncio.Task` handles, `startup`/`shutdown`/`serve`, per-task `stop`/`start`/`restart_task_runner`, control-plane snapshots (`drain_status`/`task_pause_status`/`task_control_snapshot(s)`/`task_supported_commands`/`task_resume_status`), signal handlers, and the opened-`_resources` list. Holds module-level `_open_resource`/`_close_resource`.
- `src/onestep/runtime/task_ops.py` `TaskOperations(app)` owns dead-letter replay/discard, one-shot manual run, capability probes (`supports_*_commands`, `_task_supports_*`), and `_SyntheticManualRunDelivery`.
- `src/onestep/runtime/event_hub.py` `EventHub(app)` owns startup/shutdown hooks, event handlers, `emit_event`, and structured event logging.
- Facade keeps read-only `_runners`/`_runner_tasks`/`_resources` properties and an `_install_signal_handlers()` passthrough because `tests/contract/test_runtime_contract.py` reads them directly. `serve()`/`request_drain()`/`request_task_pause()`/`request_shutdown()` and `**app.describe()` structures are byte-for-byte unchanged.

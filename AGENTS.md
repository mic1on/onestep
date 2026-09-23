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
- Legacy `onestep-mysql` (0.7.0) / `onestep-postgres` (0.6.0) are thin forwarding shims: no `onestep.resources` entry point, depend on `onestep-sql[mysql,sqlite]` / `onestep-sql[postgres,sqlite]`. The single `sql` entry point on `onestep-sql` registers all 15 YAML types, so new+old install permutations never double-register. `from onestep_mysql import ...` / `from onestep_postgres import ...` keep working via `sys.modules` forwarders with object identity.
- Shared SQL behaviour lives once in `plugins/onestep-sql/src/onestep_sql/_shared/` (`state_sqlalchemy`, `table_sink_policy`, `state_keys`, `resilience`, `execution`) — internal shared modules, not public API. `execution` hosts the tracked-execution state machine (machine / dialect / source); schema/DDL stays in each backend (tracked-execution spec §7.2). Backend adapters keep only driver mapping, install hints, dialect error-classification tables, `__init__` validation order, `_build_statement` SQL dialect branches, binlog (mysql) and tracked execution (postgres + mysql).
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

## Machine-readable CLI contracts (AI-friendly surface)

- `src/onestep/schema.py` **derives** the `onestep/v1alpha1` JSON Schema from the `_STRICT_*` field sets in `config.py` plus the live resource catalog. Published copy: `docs/public/schema/v1alpha1.json` (served at the `$id` `https://onestep.code05.com/schema/v1alpha1.json`). Regenerate with `onestep schema --out docs/public/schema/v1alpha1.json`; `tests/contract/test_app_schema.py` fails if the copy is stale.
- The schema uses `handler.allowed_fields`, **not** catalog `fields`/`required`. The catalog is a superset: it advertises `host`/`username`/`password` for `mysql`/`postgres`/`rabbitmq`/`redis` and marks `dsn`/`path` required, but strict validation rejects those fields and accepts their absence. Building the schema from the catalog would reject valid configs.
- `$schema` is a permitted top-level YAML key (documentation-only, validated as a non-empty string).
- `check --strict --json` collects **all** problems via `collect_app_config_issues` → `AppConfigValidationError` (each issue has `path`/`message`/`kind`). The human path stays fail-fast: `validate_app_config` raises the original exception type/message, and `tests/contract/test_config_validation_issues.py` pins the first collected issue to the fail-fast message. Many tests in `tests/test_cli.py` assert exact error text — do not change the default messages.
- `--json` output carries `schema`/`version`: `onestep/check-summary`, `onestep/cli-error`, plus the pre-existing `onestep/diagnostic-result` / `onestep/connectivity-result`. Failures print a JSON error envelope on stdout *and* the human line on stderr.
- Exit codes: `0` success, `1` runtime failure (`run` raised, `check --connect` probe failed), `2` invalid input/config (strict validation, load failure, bad args). Documented in `docs/guide/ai-interfaces.md`.
- `_normalize_argv` in `cli.py` allowlists subcommand names; a new subcommand must be added there or it is rewritten to `run <name>`.
- Adding a test dependency means updating `uv.lock` (`uv lock`); `jsonschema` is test-only and validates the schema in `tests/contract/test_app_schema.py`.

## Control-plane alerts and metrics

- Alert rules: `apps/control-plane/monitoring/prometheus/rules/control-plane.yml`; runbooks in `apps/control-plane/docs/runbooks/alerts.md` (one `## Heading` per alert, linked by GitHub heading slug).
- **A rule is a promise that a series exists.** `backend/tests/test_prometheus_exporter.py::test_every_alert_rule_metric_is_emitted_by_the_exporter` parses the rule file and asserts every `onestep_control_plane_*` series it references is one `/metrics` can emit; a companion test asserts every `runbook:` anchor resolves to a real heading. Both exist because three rules once referenced series no code emitted and nothing failed — add the emitter and the rule together, or CI will tell you.
- New metric families go in `ops/observability.py` and must keep labels bounded by construction: declare the allowed values, collapse anything unrecognised to `other`. Prefer emitting zero-valued series for declared label values — a ratio alert whose denominator series is absent returns "no data", not 0.
- Counter state is process-global (correct for Prometheus), so tests assert before/after deltas, not absolute values.
- `onestep_control_plane_ui_ws_disconnects_total` counts **SSE** teardowns (`GET /api/v1/ui/stream`), not websockets. The name is historical; the rule and dashboard reference it.
- `refresh_pool_occupancy` is called from the scrape path (`_compose_prometheus_metrics`); the occupancy gauges are scrape-time samples, not background ones.
- Backend tests must run from `apps/control-plane` (not `backend/`), or `conftest.py` fails to locate `backend/alembic`. `aiosqlite` and `greenlet` are dev deps the DB-backed fixtures need.

## Control-plane notification plane (Plane B)

- Two independent alert planes exist. **Plane A** = Prometheus rules (see above). **Plane B** = the notification subsystem: `notification_service.py` scans/derives events → `NotificationOutbox` → webhook to Feishu / WeCom / custom. Plane B is driven entirely by the control plane itself, so if the control plane is down, Plane B is silent — it cannot report its own outage.
- Channel classification is three independent axes: `event_types_json` (6 event types), `service_scopes_json` (empty list = **all** services, including future ones), `missed_start_grace_seconds` (only for `task_missed_start`).
- Damping is per-event-kind and deliberately asymmetric: connectivity flips use stable confirmation + flap episodes (`NotificationInstanceState`, #196); `task_failed` uses burst damping per (channel, service) (`NotificationTaskFailureBurst`); `task_started`/`task_succeeded`/`task_missed_start` are NOT damped (bounded by the scheduler or the slot dedupe key). Damping state is persisted so a restart mid-incident cannot reset a counter and resume a storm.
- A suppressed burst still reports itself: the summary carries `suppressed_failure_count`, because silence must never read as health. Keep that property when adding damping.
- `POST /channels/{id}/test` performs a **real** webhook request and returns `delivered` / `response_status_code` / `error_message`. It must never go back to preview-only: it once returned `status="accepted"` while the console said "Test accepted", so a broken channel looked healthy until an incident.
- Delivery outcomes are readable at `GET /deliveries` and surfaced in the console — this is the only feedback loop for "did my alert actually go out?". Deleting a channel nulls `channel_id` on its deliveries rather than dropping history.
- Frontend tests for `NotificationSettingsPage` route `fetch` by URL, not by call order, because the page issues three mount-time requests (channels, services, deliveries).

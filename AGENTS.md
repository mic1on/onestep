# Project agent memory

This file is the project's committed home for project-intrinsic agent knowledge: build, test, release, architecture, and sharp-edge notes that should travel with the code.

- Add durable project-specific notes here as they are discovered through real work.

## Control plane (`apps/control-plane/`)

- **Run tests:** `cd apps/control-plane && uv sync --extra test && .venv/bin/python -m pytest backend/tests` (testpaths = `backend/tests`; SQLite in-memory via `conftest.py`). Lint with `.venv/bin/ruff check`; `ruff format` is **not** enforced (the tree is not format-conformant).
- **DB migrations:** Alembic under `backend/alembic/versions/`, `down_revision` chains to the current head. Tests assert the exact table set and a `HEAD_REVISION` constant in `test_migrations.py` — update both when adding a table/migration. Models live in `backend/src/onestep_control_plane_api/db/models.py`; tests use `Base.metadata.create_all`, so model + migration must agree.
- **Background workers** are leader-gated asyncio tasks (`workers/leader.py` `PostgresAdvisoryLockLease`/`LocalWorkerLease`). To add one: write `workers/<name>_worker.py` mirroring `notification_scanner.py`/`retention_worker.py`, register it in `main.py` `lifespan` + `background_task_refs`, and add its name to `ops/readiness.build_default_background_task_states()` and `tests/conftest.py` `background_task_refs`. `tests/test_worker_leadership.py` is the pattern for leader-gating tests.
- **Notification delivery is async via an outbox** (`db.models.NotificationOutbox`, drained by `workers/notification_outbox_worker.run_notification_outbox_worker`). The telemetry receive path (`api/routers/agent_ws.py` → `agent_ingestion_service.ingest_events_request` → `notification_service.dispatch_runtime_task_event_notifications`) only inserts outbox rows and returns; the blocking `httpx` POSTs run off the event loop in `asyncio.to_thread`. Never call `notification_service._post_webhook` from a request/scan path — enqueue instead. Delivery is at-least-once (webhooks must be idempotent).

## Maintaining this file

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

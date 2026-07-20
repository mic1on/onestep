# PostgreSQL Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first-version `onestep-postgres` plugin with PostgreSQL connector, table queue source, incremental source, table sink, SQLAlchemy state/cursor stores, YAML resource registration, and focused tests.

**Architecture:** Add a repo-local plugin package parallel to `plugins/onestep-mysql`. Reuse the MySQL plugin's runtime contracts for table queue, incremental polling, and state stores, but omit binlog/CDC and use PostgreSQL dialect upsert for table sinks.

**Tech Stack:** Python, onestep resource registry entry points, SQLAlchemy 2.x, psycopg 3, pytest, uv workspace.

---

## File Structure

- Create `plugins/onestep-postgres/pyproject.toml`: package metadata, dependencies, and `onestep.resources` entry point.
- Create `plugins/onestep-postgres/README.md`: minimal install and YAML usage docs.
- Create `plugins/onestep-postgres/src/onestep_postgres/__init__.py`: public exports and `register` entry point alias.
- Create `plugins/onestep-postgres/src/onestep_postgres/connector.py`: connector, table queue source, incremental source, and table sink.
- Create `plugins/onestep-postgres/src/onestep_postgres/resources.py`: YAML build handlers for `postgres*` resource types.
- Create `plugins/onestep-postgres/src/onestep_postgres/resilience.py`: SQLAlchemy error normalization with backend name `postgres`.
- Create `plugins/onestep-postgres/src/onestep_postgres/state_sqlalchemy.py`: SQLAlchemy state/cursor store.
- Create tests under `plugins/onestep-postgres/tests/`, mirroring the MySQL plugin's non-binlog tests.
- Modify `pyproject.toml`: add the plugin to workspace members, optional extras, and uv sources.
- Modify `uv.lock`: refresh lock metadata after adding the workspace package.

### Task 1: Scaffold Package And Metadata

**Files:**
- Create: `plugins/onestep-postgres/pyproject.toml`
- Create: `plugins/onestep-postgres/README.md`
- Create: `plugins/onestep-postgres/src/onestep_postgres/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Add package metadata**

Create `plugins/onestep-postgres/pyproject.toml` with:

```toml
[project]
name = "onestep-postgres"
version = "0.1.0"
description = "PostgreSQL connector plugin for onestep."
readme = "README.md"
requires-python = ">=3.9"
license = { text = "MIT" }
dependencies = [
    "onestep>=1.4.2",
    "SQLAlchemy>=2.0.0",
    "psycopg[binary]>=3.2.0",
]

[project.optional-dependencies]
test = ["pytest>=8.0.0"]
dev = ["pytest>=8.0.0"]

[project.entry-points."onestep.resources"]
postgres = "onestep_postgres:register"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/onestep_postgres"]

[tool.uv.sources]
onestep = { workspace = true }
```

- [ ] **Step 2: Add public exports**

Create `plugins/onestep-postgres/src/onestep_postgres/__init__.py` exporting `PostgresConnector`, sources, deliveries, sink, state stores, resilience helpers, and `register = register_resources`.

- [ ] **Step 3: Add README**

Create a short README with install command, YAML example, and note that CDC/logical replication is not part of v1.

- [ ] **Step 4: Register workspace package**

Update root `pyproject.toml`:

```toml
postgres = [
    "onestep-postgres>=0.1.0",
]
```

Also add `onestep-postgres>=0.1.0` to `integration`, `all`, and `dev`; add `plugins/onestep-postgres` to `[tool.uv.workspace].members`; add `onestep-postgres = { workspace = true }` to `[tool.uv.sources]`.

- [ ] **Step 5: Validate metadata**

Run: `uv lock`

Expected: `uv.lock` updates successfully and includes `onestep-postgres`.

### Task 2: Implement Runtime Code

**Files:**
- Create: `plugins/onestep-postgres/src/onestep_postgres/connector.py`
- Create: `plugins/onestep-postgres/src/onestep_postgres/state_sqlalchemy.py`
- Create: `plugins/onestep-postgres/src/onestep_postgres/resilience.py`

- [ ] **Step 1: Copy the SQLAlchemy state store shape**

Implement `SQLAlchemyStateStore` and `SQLAlchemyCursorStore` with async `load`, `save`, `delete`, and `close`, matching the MySQL plugin's behavior.

- [ ] **Step 2: Add PostgreSQL connector**

Implement `PostgresConnector(dsn, **engine_options)` with `state_store`, `cursor_store`, `table_queue`, `incremental`, `table_sink`, `_table`, and `close`.

- [ ] **Step 3: Add table queue source**

Implement `PostgresTableQueueSource` with `fetch_is_cancel_safe = False`, `FOR UPDATE SKIP LOCKED` claiming, and delivery methods `ack`, `retry`, `fail`, `release_unstarted`, and `update_current_row`.

- [ ] **Step 4: Add incremental source**

Implement `PostgresIncrementalSource` with cursor state loading, tuple cursor comparison, pending/acked cursor commit ordering, and default state key hashing for long `where` clauses.

- [ ] **Step 5: Add table sink**

Implement `PostgresTableSink` supporting `insert` and `upsert`. Use `sqlalchemy.dialects.postgresql.insert(...).on_conflict_do_update(...)` for PostgreSQL, keep SQLite support in tests, and require `keys` for upsert.

- [ ] **Step 6: Add resilience helper**

Implement `classify_sqlalchemy_error` and `as_postgres_connector_operation_error` with backend name `postgres`, following the MySQL plugin's error normalization contract.

### Task 3: Implement YAML Resource Registration

**Files:**
- Create: `plugins/onestep-postgres/src/onestep_postgres/resources.py`

- [ ] **Step 1: Register resource handlers**

Register `postgres`, `postgres_state_store`, `postgres_cursor_store`, `postgres_table_queue`, `postgres_incremental`, and `postgres_table_sink`.

- [ ] **Step 2: Build resources from specs**

Implement builders using `ResourceBuildContext` helpers:

```python
connector = ctx.resolve_dependency(spec, "connector")
table = ctx.require_string(spec, "table")
cursor = tuple(ctx.string_list(spec.get("cursor"), field=f"{ctx.field}.cursor"))
```

- [ ] **Step 3: Keep strict field sets narrow**

Allowed fields should match the implemented API: connector DSN plus `engine_options`; state/cursor store options; table queue claim fields; incremental state fields; table sink mode and keys.

### Task 4: Add Tests

**Files:**
- Create: `plugins/onestep-postgres/tests/test_postgres_plugin.py`
- Create: `plugins/onestep-postgres/tests/test_postgres_incremental.py`
- Create: `plugins/onestep-postgres/tests/test_postgres_table_queue.py`
- Create: `plugins/onestep-postgres/tests/test_postgres_runtime_contract.py`
- Create: `plugins/onestep-postgres/tests/test_state_sqlalchemy.py`
- Create: `plugins/onestep-postgres/tests/integration/test_postgres_live.py`

- [ ] **Step 1: Add plugin/resource tests**

Cover entry point discovery and strict YAML construction with `load_app_config`, asserting resource instances are `PostgresConnector`, `SQLAlchemyStateStore`, `SQLAlchemyCursorStore`, `PostgresIncrementalSource`, and `PostgresTableSink`.

- [ ] **Step 2: Add incremental tests**

Cover ordered cursor advancement, out-of-order ack gap behavior, key tie-breaker behavior, and default state key separation for distinct `where` clauses.

- [ ] **Step 3: Add table queue tests**

Cover a round trip through `OneStepApp`, `ctx.update_current_row`, `ack`, and `PostgresTableSink` upsert.

- [ ] **Step 4: Add runtime cancellation contract tests**

Cover drain, pause, and shutdown releasing claimed table queue rows when fetch is stopped before delivery starts.

- [ ] **Step 5: Add live integration tests**

Use `ONESTEP_POSTGRES_DSN`; skip at module level when unset. Cover table queue claim/ack/retry, incremental restart recovery, and PostgreSQL upsert.

### Task 5: Validate And Commit

**Files:**
- Modify: `uv.lock`
- Test all created plugin files.

- [ ] **Step 1: Run focused tests**

Run: `uv run --all-packages python -m pytest -q plugins/onestep-postgres/tests`

Expected: all non-live plugin tests pass; live tests skip if `ONESTEP_POSTGRES_DSN` is unset.

- [ ] **Step 2: Run lock check**

Run: `uv lock --check`

Expected: lockfile is current.

- [ ] **Step 3: Review diff**

Run: `git diff --stat` and `git diff --check`

Expected: only PostgreSQL plugin, root metadata, lockfile, and plan/spec docs are changed; no whitespace errors.

- [ ] **Step 4: Commit implementation**

```bash
git add pyproject.toml uv.lock plugins/onestep-postgres docs/superpowers/plans/2026-06-14-postgres-plugin.md
git commit -m "feat: add postgres plugin"
```

## Self-Review

- Spec coverage: package, resource registration, table queue, incremental source, table sink, state/cursor store, tests, workspace metadata, and lockfile are covered.
- Explicit non-goals: no CDC/logical replication, no onestep-web changes, no control-plane protocol changes.
- Placeholder scan: no TBD/TODO steps remain.
- Type consistency: the public prefix is `Postgres*`, package import is `onestep_postgres`, resource type prefix is `postgres_*`.

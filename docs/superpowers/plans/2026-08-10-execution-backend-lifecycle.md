# Execution Backend Lifecycle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make tracked execution backends concise and process-safe by allowing direct DSN construction, hiding connector ownership behind the backend/client lifecycle, and preserving an advanced shared-connector path.

**Architecture:** `ExecutionClient` remains the backend-independent business facade. `ExecutionBackend` gains an async lifecycle contract and `ExecutionClient` becomes an async context manager. `PostgresExecutionBackend` accepts a DSN as the normal entry point, creates its connector lazily inside the current process, and keeps `from_connector()` plus `PostgresConnector.execution_backend()` for shared-pool compatibility.

**Tech Stack:** Python 3.9+, asyncio, typing Protocols, SQLAlchemy, psycopg/SQLite test backend, pytest, VitePress documentation.

---

### Task 1: Lock the core lifecycle contract

**Files:**
- Modify: `src/onestep/execution.py`
- Modify: `tests/test_execution.py`
- Modify: `src/onestep/__init__.py` only if a new lifecycle-facing public type is exported

- [ ] **Step 1: Add a fake lifecycle backend test**

Extend the test fake with `open()` and `close()` counters. Add a test that:

```python
async with ExecutionClient(backend, namespace="agent-api") as client:
    assert client is client
assert backend.open_count == 1
assert backend.close_count == 1
```

Also assert that an exception raised inside the context still closes the backend.

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `uv run pytest -q tests/test_execution.py`

Expected: failure because `ExecutionClient` has no async context manager.

- [ ] **Step 3: Implement the minimal lifecycle API**

Add `open()` and `close()` to the `ExecutionBackend` Protocol, then add:

```python
async def __aenter__(self) -> "ExecutionClient":
    await self.backend.open()
    return self

async def __aexit__(self, exc_type, exc_value, traceback) -> None:
    await self.backend.close()
```

Keep regular client operations usable without a context so existing lightweight fakes and lazy backends remain compatible. The context manager is the standard lifecycle path for applications.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `uv run pytest -q tests/test_execution.py`

Expected: all execution client tests pass.

### Task 2: Add the direct PostgreSQL backend constructor

**Files:**
- Modify: `plugins/onestep-postgres/src/onestep_postgres/execution_backend.py`
- Modify: `plugins/onestep-postgres/src/onestep_postgres/connector.py`
- Modify: `plugins/onestep-postgres/tests/test_postgres_plugin.py`
- Modify: `plugins/onestep-postgres/tests/test_postgres_execution_backend.py`

- [ ] **Step 1: Add constructor and compatibility tests**

Test the normal path with a SQLite DSN:

```python
backend = PostgresExecutionBackend(
    dsn=f"sqlite:///{tmp_path / 'execution.db'}",
    auto_create=True,
)
assert backend.connector is not created until `open()` or the first operation
```

Test the advanced path:

```python
connector = PostgresConnector(...)
backend = PostgresExecutionBackend.from_connector(connector, auto_create=True)
assert backend.connector is connector
assert connector.execution_backend() returns an equivalent backend
```

- [ ] **Step 2: Run the focused plugin tests and verify the direct path fails**

Run: `uv run pytest -q plugins/onestep-postgres/tests/test_postgres_plugin.py plugins/onestep-postgres/tests/test_postgres_execution_backend.py`

Expected: failure because the backend only accepts `connector=`.

- [ ] **Step 3: Implement lazy owned connector creation**

Make the constructor accept exactly one of `dsn` or `connector`. Store DSN and engine options without creating an engine. Add `from_connector()` as the explicit external-ownership constructor. Make `PostgresConnector.execution_backend()` delegate to `PostgresExecutionBackend.from_connector(self, ...)`.

Expose `connector` and `engine` as lazy properties for existing plugin internals and tests. On the direct DSN path, create `PostgresConnector(dsn, **engine_options)` inside the backend's synchronous readiness path, before `create_all()` or table inspection. On the external connector path, reuse the supplied object unchanged.

- [ ] **Step 4: Implement ownership-aware close**

Make `backend.close()` idempotent. It disposes a connector created from DSN, but does not close a connector supplied through `from_connector()`. After closing an owned backend, the next `open()` may recreate its connector.

- [ ] **Step 5: Run the focused plugin tests and verify they pass**

Run: `uv run pytest -q plugins/onestep-postgres/tests/test_postgres_plugin.py plugins/onestep-postgres/tests/test_postgres_execution_backend.py`

Expected: all direct-constructor, compatibility, and existing backend tests pass.

### Task 3: Make direct construction fork-safe

**Files:**
- Modify: `plugins/onestep-postgres/src/onestep_postgres/execution_backend.py`
- Modify: `plugins/onestep-postgres/tests/test_postgres_execution_backend.py`

- [ ] **Step 1: Add process-boundary regression tests**

Test that a direct backend constructed before a simulated PID change creates a connector in the new process context instead of reusing the inherited engine. Test that an externally supplied connector used after a PID change raises a clear error instructing the caller to create the connector in the child process.

- [ ] **Step 2: Run the focused tests and verify the process tests fail**

Run: `uv run pytest -q plugins/onestep-postgres/tests/test_postgres_execution_backend.py -k process`

Expected: failure because the backend currently has no process ownership check.

- [ ] **Step 3: Add PID-aware lazy initialization**

Record the creating PID. Before connector access and readiness, compare `os.getpid()` with the recorded PID. For an owned DSN backend, discard the inherited connector and recreate it in the child; dispose any inherited SQLAlchemy pool with `engine.dispose(close=False)` so the child does not close parent-owned file descriptors. For an external connector, raise instead of silently sharing a forked pool.

Keep all database coordination in PostgreSQL transactions and lease CAS conditions; process isolation only controls connection-pool ownership.

- [ ] **Step 4: Run the focused process tests and verify they pass**

Run: `uv run pytest -q plugins/onestep-postgres/tests/test_postgres_execution_backend.py -k process`

Expected: all process-boundary tests pass.

### Task 4: Update application examples and plugin documentation

**Files:**
- Modify: `docs/broker/postgres-execution.md`
- Modify: `docs/broker/postgres.md`
- Modify: `plugins/onestep-postgres/README.md`
- Modify: `skills/onestep/references/connectors.md` if its execution example is still connector-first

- [ ] **Step 1: Replace the recommended API example**

Use `PostgresExecutionBackend(dsn=...)` and `async with executions` in the API lifespan. Remove the visible `backend.open()`/`pg.close()` pairing from the primary example.

- [ ] **Step 2: Document the advanced shared-connector path**

Explain that `PostgresConnector` remains available for table queues, sinks, state stores, or connection-pool sharing, and show `PostgresExecutionBackend.from_connector(pg, ...)`. State that externally supplied connectors are owned and closed by the caller.

- [ ] **Step 3: Document multiprocessing rules**

State that every API/worker process owns an independent backend pool, the DSN constructor is lazy and fork-safe, `worker_id` must be unique, and total database connections must be sized across all processes.

- [ ] **Step 4: Build the documentation**

Run: `pnpm build` from `docs/`

Expected: VitePress build completes successfully.

### Task 5: Full validation and handoff

**Files:**
- Review all modified files and the final diff

- [ ] **Step 1: Run focused core and plugin tests**

Run: `uv run --all-packages pytest -q tests/test_execution.py plugins/onestep-postgres/tests/test_postgres_plugin.py plugins/onestep-postgres/tests/test_postgres_execution_backend.py plugins/onestep-postgres/tests/test_postgres_execution_source.py`

Expected: all selected tests pass.

- [ ] **Step 2: Run reliability checks**

Run: `./scripts/run-reliability-checks.sh`

Expected: core and plugin reliability suites pass; integration tests remain environment-dependent.

- [ ] **Step 3: Review formatting and protected files**

Run: `git diff --check` and `git status --short`. Confirm the four pre-existing untracked plan/spec/Lavish files are unchanged and are not staged.

- [ ] **Step 4: Commit and push only the implementation/documentation changes**

Stage the modified core, PostgreSQL plugin, tests, and user-facing docs explicitly. Do not stage the pre-existing untracked planning/spec/Lavish files or generated build output. Push the commit to `feat/postgres-execution-backend` so PR #111 receives the update.

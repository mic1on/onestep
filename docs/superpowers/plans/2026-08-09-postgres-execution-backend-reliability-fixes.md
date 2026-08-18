# PostgreSQL Execution Backend Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the six validated lease, completion, startup, and source-reliability defects in the PostgreSQL execution backend.

**Architecture:** Keep the existing backend and delivery boundaries. Strengthen database state transitions with conditional updates, enforce lease deadlines at every worker write, make completion idempotency compare persisted business output, and reuse the plugin's existing connector-error normalization.

**Tech Stack:** Python 3.11+, asyncio, SQLAlchemy, pytest, PostgreSQL 16, uv.

---

### Task 1: Lock Down Lease Validity And Cleanup Races

**Files:**
- Modify: `plugins/onestep-postgres/src/onestep_postgres/execution_backend.py`
- Test: `plugins/onestep-postgres/tests/test_postgres_execution_source.py`
- Test: `plugins/onestep-postgres/tests/integration/test_postgres_execution_live.py`

- [x] **Step 1: Add expired-token regression tests**

Add a parametrized test that advances the mutable clock beyond `lease_expires_at` and asserts `heartbeat`, `complete`, and `release` each raise `StaleExecutionLease` without changing the running execution.

- [x] **Step 2: Add a real PostgreSQL cleanup race test**

Pause expired-running cleanup after its stale read, renew from an independent backend, resume cleanup, and assert the renewal is not overwritten. Coordinate the interleaving with threading events and SQLAlchemy connection hooks.

- [x] **Step 3: Guard cleanup transitions**

For queued expiry, cancel-request expiry, and running lease release, condition the execution update on the selected version, expected status, token where applicable, and the relevant deadline. Update the attempt only after the execution transition wins.

- [x] **Step 4: Enforce lease deadlines on worker writes**

Add `lease_expires_at > now` to heartbeat, completion, and release execution predicates.

- [x] **Step 5: Run focused lease tests**

Run:

```bash
uv run --all-packages pytest -q plugins/onestep-postgres/tests/test_postgres_execution_source.py
```

Expected: all source/backend lease unit tests pass.

### Task 2: Preserve Delivery Completion Fallback

**Files:**
- Modify: `plugins/onestep-postgres/src/onestep_postgres/execution_source.py`
- Test: `plugins/onestep-postgres/tests/test_postgres_execution_source.py`

- [x] **Step 1: Add a failed-completion retry test**

Use a backend double whose first completion raises and whose second succeeds. Assert the second completion reaches the backend with `retrying`.

- [x] **Step 2: Move the completion marker after commit**

Set `_completed = True` only after `backend.complete()` returns successfully.

- [x] **Step 3: Run the focused delivery test**

Run:

```bash
uv run --all-packages pytest -q plugins/onestep-postgres/tests/test_postgres_execution_source.py -k completion
```

Expected: the failed first completion remains retryable and successful duplicate calls remain no-ops.

### Task 3: Normalize Source Fetch Failures

**Files:**
- Modify: `plugins/onestep-postgres/src/onestep_postgres/execution_source.py`
- Test: `plugins/onestep-postgres/tests/test_postgres_execution_source.py`

- [x] **Step 1: Add an OperationalError regression test**

Make `backend.claim()` raise SQLAlchemy `OperationalError` and assert `fetch()` raises a transient PostgreSQL `ConnectorOperationError` with operation `FETCH`, source name, and poll delay.

- [x] **Step 2: Reuse PostgreSQL error normalization**

Wrap claim failures with `as_postgres_connector_operation_error`, passing connector secret tokens for redaction, while preserving unclassified exceptions.

- [x] **Step 3: Run focused source tests**

Run:

```bash
uv run --all-packages pytest -q plugins/onestep-postgres/tests/test_postgres_execution_source.py -k source
```

Expected: dependency failures are normalized and ordinary programming errors retain their original type.

### Task 4: Make Terminal Completion Idempotency Exact

**Files:**
- Modify: `plugins/onestep-postgres/src/onestep_postgres/execution_backend.py`
- Test: `plugins/onestep-postgres/tests/test_postgres_execution_source.py`

- [x] **Step 1: Add matching and mismatched replay tests**

Assert an identical terminal completion returns the stored snapshot, while the same status with different result or error raises `StaleExecutionLease`.

- [x] **Step 2: Re-read and compare persisted output**

After a failed completion CAS, read the current row in the same transaction and compare encoded result/error before accepting an idempotent replay.

- [x] **Step 3: Run focused completion tests**

Run:

```bash
uv run --all-packages pytest -q plugins/onestep-postgres/tests/test_postgres_execution_source.py -k complete
```

Expected: only business-identical terminal replays succeed.

### Task 5: Serialize Automatic Schema Creation

**Files:**
- Modify: `plugins/onestep-postgres/src/onestep_postgres/execution_backend.py`
- Test: `plugins/onestep-postgres/tests/integration/test_postgres_execution_live.py`

- [x] **Step 1: Add a concurrent cold-open regression test**

Open two independent backends concurrently against fresh table names and assert both complete without DDL integrity errors.

- [x] **Step 2: Add a transaction-scoped PostgreSQL advisory lock**

For PostgreSQL `auto_create=True`, acquire `pg_advisory_xact_lock` using a stable key derived from the execution and attempt table names before `create_all(checkfirst=True)`. Keep non-PostgreSQL behavior unchanged.

- [x] **Step 3: Run live PostgreSQL tests**

Run:

```bash
ONESTEP_POSTGRES_DSN=postgresql+psycopg://onestep:onestep@127.0.0.1:5432/onestep \
  uv run --all-packages pytest -q -m integration \
  plugins/onestep-postgres/tests/integration/test_postgres_execution_live.py
```

Expected: all live execution tests pass, including repeated concurrent cold opens.

### Task 6: Full Validation

**Files:**
- Verify only; no additional files.

- [x] **Step 1: Run PostgreSQL plugin tests**

```bash
uv run --all-packages pytest -q -m "not integration" plugins/onestep-postgres/tests
```

- [x] **Step 2: Run core reliability checks**

```bash
./scripts/run-reliability-checks.sh
```

- [x] **Step 3: Review the final diff**

```bash
git diff --check
git diff -- plugins/onestep-postgres/src plugins/onestep-postgres/tests
```

Expected: no whitespace errors, no unrelated changes, and all six regressions covered.

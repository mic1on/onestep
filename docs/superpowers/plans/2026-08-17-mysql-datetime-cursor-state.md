# MySQL Datetime Cursor State Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist and restore MySQL incremental cursor `datetime` components without changing generic application-state JSON behavior.

**Architecture:** `SQLAlchemyCursorStore` will override only its `save` and `load` boundary. It encodes each top-level `datetime` cursor component as an explicitly tagged ISO-8601 JSON object, delegates storage to `SQLAlchemyStateStore`, and restores only that exact marker on load. Existing untagged JSON cursor values remain unchanged.

**Tech Stack:** Python 3.9+, SQLAlchemy async storage, pytest, SQLite test database.

---

### Task 1: Reproduce cursor persistence and restart behavior

**Files:**
- Modify: `plugins/onestep-mysql/tests/test_state_sqlalchemy.py`
- Modify: `plugins/onestep-mysql/tests/test_mysql_incremental.py`

- [x] **Step 1: Write a cursor-store round-trip regression test**

Save `[datetime(2026, 8, 17, 0, 53, 55, 640000), "u_123"]` through
`MySQLConnector.cursor_store()`. Assert the cursor store restores the datetime
component, the generic state store sees the tagged JSON representation, and an
existing numeric/string cursor still loads unchanged.

- [x] **Step 2: Write a source restart regression test**

Create a SQLite table with a `DateTime` first cursor component and an integer
tie-breaker. Ack a two-row batch, reopen the connector and source, then insert a
later row. Assert the reopened source returns only the later row.

- [x] **Step 3: Run the focused tests before implementation**

Run:

```bash
uv run --package onestep-mysql pytest -q \
  plugins/onestep-mysql/tests/test_state_sqlalchemy.py::test_mysql_cursor_store_round_trips_datetime_cursor_values \
  plugins/onestep-mysql/tests/test_mysql_incremental.py::test_mysql_incremental_restarts_from_datetime_cursor
```

Expected: both tests fail with `TypeError: Object of type datetime is not JSON serializable`.

### Task 2: Add cursor-only tagged datetime codec

**Files:**
- Modify: `plugins/onestep-mysql/src/onestep_mysql/state_sqlalchemy.py`

- [ ] **Step 1: Implement the codec in `SQLAlchemyCursorStore`**

Override its `save()` to replace only top-level `datetime` components with:

```python
{
    "__onestep_cursor_type__": "datetime",
    "value": value.isoformat(),
}
```

Override `load()` to restore only a mapping with exactly those two keys, marker
value `"datetime"`, and a string `value`, using `datetime.fromisoformat()`.
All untagged values delegate unchanged, preserving old persisted cursor state.

- [ ] **Step 2: Run the focused regression tests**

Run the command from Task 1. Expected: `2 passed`.

### Task 3: Release metadata and validation

**Files:**
- Modify: `plugins/onestep-mysql/pyproject.toml`
- Modify: `uv.lock`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Mark the compatible patch release**

Change the plugin version from `0.5.0` to `0.5.1`, regenerate the workspace lock
entry, and add a changelog entry explaining that persisted incremental cursor
datetimes now round-trip as tagged ISO-8601 values.

- [ ] **Step 2: Validate the plugin**

Run:

```bash
uv run --package onestep-mysql pytest -q plugins/onestep-mysql/tests
uv build --package onestep-mysql
uv run twine check dist/onestep_mysql-0.5.1*
git diff --check
```

Expected: tests, package build, metadata check, and whitespace check pass.

### Task 4: Publish the review branch

**Files:**
- Modify: the files from Tasks 1–3

- [ ] **Step 1: Commit the fixed implementation**

```bash
git add CHANGELOG.md uv.lock plugins/onestep-mysql
git commit -m "fix(mysql): persist datetime incremental cursors"
```

- [ ] **Step 2: Push and open the pull request**

```bash
git push -u origin fix/mysql-datetime-cursor-state
gh-axi pr create --base main --head fix/mysql-datetime-cursor-state \
  --title "fix(mysql): persist datetime incremental cursors"
```

Expected: a PR URL with the focused regression coverage and release impact.

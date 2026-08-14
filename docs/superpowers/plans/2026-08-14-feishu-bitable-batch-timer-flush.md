# Feishu Bitable Batch Timer Flush Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure a Feishu Bitable sink with `batch_size > 1` submits a partial batch when its flush timer expires.

**Architecture:** Keep the existing sink buffer and Feishu `batch_create`/`batch_update` APIs. The timer must retain its own task identity while it runs; only a separately scheduled timer is cancelled when a synchronous threshold or close flush takes over.

**Tech Stack:** Python 3.9+, asyncio, pytest, local JSON HTTP test server.

---

### Task 1: Reproduce automatic partial-batch flush

**Files:**
- Modify: `plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py`

- [ ] **Step 1: Add a failing HTTP integration-style unit test**

Create a `mode="create"`, `batch_size=2`, `flush_interval_s=0.01` sink, send one envelope, wait beyond the interval, and assert one `/batch_create` request contains that record before `close()` is called.

- [ ] **Step 2: Run the focused test to verify the current failure**

Run: `uv run --all-packages python -m pytest -q plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py -k automatic_partial_batch`

Expected: FAIL because the timer cancels itself before the batch request reaches the server.

### Task 2: Preserve the running timer during automatic flush

**Files:**
- Modify: `plugins/onestep-feishu-bitable/src/onestep_feishu_bitable/connector.py`

- [ ] **Step 1: Make timer cleanup task-identity-safe**

Compare `self._flush_task` to `asyncio.current_task()` before cancellation. A timer calling `_flush_buffer()` must not cancel or clear its own task reference; timer completion and cancellation handlers clear the reference only when it still points to that same timer.

- [ ] **Step 2: Run the focused regression test**

Run: `uv run --all-packages python -m pytest -q plugins/onestep-feishu-bitable/tests/test_feishu_bitable_connector.py -k automatic_partial_batch`

Expected: PASS with exactly one batch create request containing the partial batch.

### Task 3: Release plugin 0.3.5

**Files:**
- Modify: `plugins/onestep-feishu-bitable/pyproject.toml`
- Modify: `CHANGELOG.md`
- Modify: `uv.lock`

- [ ] **Step 1: Bump the plugin version and document the timer-flush fix**

Set `onestep-feishu-bitable` to `0.3.5`; describe that scheduled partial-batch flushes no longer cancel themselves. Regenerate the workspace lockfile.

- [ ] **Step 2: Verify the distribution**

Run: `uv build --package onestep-feishu-bitable --out-dir dist/plugin --sdist --wheel --clear && uvx twine check dist/plugin/*`

Expected: wheel and source distribution metadata both pass.

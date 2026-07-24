# Onestep Stable Instance Identity Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make an onestep worker keep a stable logical `instance_id` across restarts while still exposing a fresh `session_id` per process start, without breaking the current control-plane ingestion ordering rules.

**Architecture:** Add a small local identity store on the onestep side. Persist `instance_id`, `heartbeat_sequence`, and `sync_sequence` in a state file guarded by a lock. Reuse `instance_id` after restart, mint a fresh `session_id` for each process, and continue `sync` / `heartbeat` sequences instead of resetting them so the current control-plane freshness checks still accept the data stream.

**Tech Stack:** Python worker runtime, local JSON state file, file lock, current control-plane WS protocol

---

## Protocol Constraints

- `service.instance_id` is defined as the stable runtime instance identity, not a per-process boot ID. See [agent-ws-protocol.md](/Users/miclon/development/onestep-control-plane/docs/protocols/agent-ws-protocol.md#L127).
- A fresh valid session for the same `instance_id` is expected to supersede the old active session. See [agent-ws-protocol.md](/Users/miclon/development/onestep-control-plane/docs/protocols/agent-ws-protocol.md#L371).
- The current control-plane decides freshness for `sync` and `heartbeat` with stored `last_*_sequence` plus `sent_at`. If onestep restarts with the same `instance_id` but resets sequence to `1`, the new data can be rejected as stale. See [agent-ws-protocol.md](/Users/miclon/development/onestep-control-plane/docs/protocols/agent-ws-protocol.md#L180) and [common.py](/Users/miclon/development/onestep-control-plane/backend/src/onestep_control_plane_api/api/common.py#L177).

## State Model

Persist one record per logical worker replica:

```json
{
  "schema_version": 1,
  "instance_id": "8d213b11-9109-4f31-8e1a-cd7f3eb9e5f8",
  "heartbeat_sequence": 142,
  "sync_sequence": 19,
  "created_at": "2026-03-19T08:30:00Z",
  "updated_at": "2026-03-19T08:45:00Z"
}
```

Rules:

- `instance_id` survives restart.
- `heartbeat_sequence` only increases.
- `sync_sequence` only increases.
- `session_id` is not persisted.
- One live process must own one state file at a time.

## Configuration Contract

Add or standardize these inputs on the onestep side:

- `ONESTEP_INSTANCE_ID`
  - Hard override. Use only for explicit pinning or tests.
- `ONESTEP_REPLICA_KEY`
  - Stable logical replica identity such as `worker-0` or StatefulSet ordinal.
- `ONESTEP_STATE_DIR`
  - Directory for `identity.json` and the lock file.

Resolution order:

1. If `ONESTEP_INSTANCE_ID` is set, use it directly.
2. Else if `ONESTEP_REPLICA_KEY` is set, derive a deterministic UUIDv5 from `service_name + environment + replica_key`.
3. Else load `instance_id` from the local state file, creating one on first boot.

Do not derive `instance_id` from:

- `pid`
- current timestamp
- random UUID per startup
- hostname alone
- pod name when it changes every rollout

## Task 1: Add Identity Store

**Files:**
- Create: `onestep/.../identity_store.py`
- Test: `tests/.../test_identity_store.py`

**Step 1: Write the failing test**

Cover:

- first boot creates a new `instance_id`
- second boot with same state dir reuses that `instance_id`
- `next_heartbeat_sequence()` returns `1`, then `2`, then persists
- `next_sync_sequence()` returns `1`, then `2`, then persists

**Step 2: Run test to verify it fails**

Run the focused identity-store test target in the onestep repo.

Expected: missing module or missing persistence behavior.

**Step 3: Write minimal implementation**

Provide:

- `load_or_create_instance_id()`
- `next_heartbeat_sequence()`
- `next_sync_sequence()`
- atomic save helper
- schema version field

**Step 4: Run test to verify it passes**

Expected: all identity-store tests pass.

**Step 5: Commit**

Commit message:

```bash
git commit -m "feat: add persistent worker identity store"
```

## Task 2: Add State File Locking

**Files:**
- Modify: `onestep/.../identity_store.py`
- Test: `tests/.../test_identity_store.py`

**Step 1: Write the failing test**

Cover:

- opening the same state dir from two live processes fails fast
- stale lock cleanup policy is explicit and tested

**Step 2: Run test to verify it fails**

Expected: both handles can currently coexist or lock behavior is missing.

**Step 3: Write minimal implementation**

Add:

- file lock next to `identity.json`
- clear error message with state dir path
- explicit release on shutdown

**Step 4: Run test to verify it passes**

Expected: second owner is rejected.

**Step 5: Commit**

```bash
git commit -m "feat: guard worker identity with file lock"
```

## Task 3: Wire Startup Identity

**Files:**
- Modify: `onestep/.../config.py`
- Modify: `onestep/.../runtime.py`
- Modify: `onestep/.../ws_client.py`
- Test: `tests/.../test_runtime_identity.py`

**Step 1: Write the failing test**

Cover:

- startup reuses persisted `instance_id`
- startup generates a fresh `session_id`
- config override precedence works as designed

**Step 2: Run test to verify it fails**

Expected: runtime still generates a fresh `instance_id` per start or has no override precedence.

**Step 3: Write minimal implementation**

Startup flow:

1. build config
2. open `IdentityStore`
3. resolve stable `instance_id`
4. create fresh `session_id`
5. pass both into WS `hello`

**Step 4: Run test to verify it passes**

Expected: restart keeps `instance_id` stable while `session_id` changes.

**Step 5: Commit**

```bash
git commit -m "feat: reuse instance identity across worker restarts"
```

## Task 4: Persist Heartbeat Sequence

**Files:**
- Modify: `onestep/.../heartbeat_reporter.py`
- Modify: `onestep/.../identity_store.py`
- Test: `tests/.../test_heartbeat_reporter.py`

**Step 1: Write the failing test**

Cover:

- first heartbeat uses next persisted sequence
- after simulated restart, next heartbeat sequence continues from previous value

**Step 2: Run test to verify it fails**

Expected: sequence resets after restart.

**Step 3: Write minimal implementation**

Before each heartbeat send:

1. fetch `next_heartbeat_sequence()`
2. stamp `sent_at`
3. send frame
4. keep the increment durable

Implementation note:

- do not reuse an in-memory counter that dies with the process

**Step 4: Run test to verify it passes**

Expected: sequence monotonicity survives restart.

**Step 5: Commit**

```bash
git commit -m "feat: persist heartbeat sequencing across restarts"
```

## Task 5: Persist Sync Sequence

**Files:**
- Modify: `onestep/.../sync_reporter.py`
- Modify: `onestep/.../identity_store.py`
- Test: `tests/.../test_sync_reporter.py`

**Step 1: Write the failing test**

Cover:

- initial sync after reconnect uses persisted next sequence
- topology-change sync also increments from last persisted value

**Step 2: Run test to verify it fails**

Expected: sync sequence restarts at `1`.

**Step 3: Write minimal implementation**

Before each sync send:

1. fetch `next_sync_sequence()`
2. stamp `sent_at`
3. send frame

**Step 4: Run test to verify it passes**

Expected: sync sequence monotonicity survives reconnect and restart.

**Step 5: Commit**

```bash
git commit -m "feat: persist sync sequencing across restarts"
```

## Task 6: Keep Restart Semantics Visible

**Files:**
- Modify: `onestep/.../runtime_descriptor.py`
- Test: `tests/.../test_runtime_descriptor.py`

**Step 1: Write the failing test**

Cover:

- `runtime.started_at` reflects current process start, not original identity creation time
- `pid` changes on restart while `instance_id` does not

**Step 2: Run test to verify it fails**

Expected: startup metadata is incorrectly coupled to identity persistence.

**Step 3: Write minimal implementation**

Keep:

- `instance_id` stable
- `session_id` fresh
- `runtime.started_at` fresh
- `pid` fresh

This preserves restart visibility without inventing new instances in control-plane.

**Step 4: Run test to verify it passes**

Expected: same logical instance, new process facts.

**Step 5: Commit**

```bash
git commit -m "fix: preserve restart visibility with stable instance identity"
```

## Task 7: Add Multi-Replica Rules

**Files:**
- Modify: `onestep/.../config.py`
- Modify: `onestep/.../bootstrap.py`
- Test: `tests/.../test_replica_identity.py`
- Docs: `docs/.../control-plane.md`

**Step 1: Write the failing test**

Cover:

- two workers with different `replica_key` values produce different `instance_id`
- two workers sharing the same state dir are rejected

**Step 2: Run test to verify it fails**

Expected: ambiguous or colliding identity behavior.

**Step 3: Write minimal implementation**

Operational rule set:

- single worker: one state dir
- multi-worker on one host: one state dir or one replica key per worker
- StatefulSet: use ordinal as replica key
- Deployment with disposable pod names: inject a stable replica key if restart continuity matters

**Step 4: Run test to verify it passes**

Expected: identity is stable per logical replica, not per service.

**Step 5: Commit**

```bash
git commit -m "docs: define stable replica identity rules"
```

## Task 8: Add End-to-End Restart Regression

**Files:**
- Test: `tests/.../test_control_plane_reconnect.py`

**Step 1: Write the failing test**

Test flow:

1. start worker with empty state dir
2. connect to control-plane
3. send `hello`, `sync`, `heartbeat`
4. capture `instance_id`, `session_id`, and last sequence values
5. restart worker with same state dir
6. reconnect and resend `hello`, `sync`, `heartbeat`
7. assert:
   - `instance_id` unchanged
   - `session_id` changed
   - `runtime.started_at` changed
   - `sync_sequence` increased
   - `heartbeat_sequence` increased
   - control-plane still shows one logical instance, not two

**Step 2: Run test to verify it fails**

Expected: duplicate instance identity or sequence regression.

**Step 3: Write minimal implementation**

Only fill gaps exposed by the test. Do not redesign protocol in this pass.

**Step 4: Run test to verify it passes**

Expected: restart is modeled as reconnect of the same instance.

**Step 5: Commit**

```bash
git commit -m "test: cover stable instance identity across restart"
```

## Rollout Notes

- This is an onestep-side change first. Control-plane does not need a protocol change for the minimum viable fix.
- Existing stale instance rows already created in control-plane will remain until separately cleaned up.
- If later you want sequences to reset on restart, add a protocol-level `runtime_id` or `incarnation_id` and then change freshness rules on the control-plane side.

## Acceptance Criteria

- Restarting one logical worker does not create a second logical instance in control-plane.
- Reconnect creates a fresh `session_id`.
- `sync` and `heartbeat` continue to ingest after restart without stale rejection.
- Multi-replica deployments still produce unique logical instances.
- Same-state-dir double start fails fast with a clear error.

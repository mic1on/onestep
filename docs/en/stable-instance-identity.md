# OneStep Stable Instance Identity Guide

This document explains how `onestep` determines `instance_id`, how to keep it stable across restarts, and how to configure it for different deployment patterns.

## 1. Which Identifiers Are Stable and Which Are Not

- `instance_id`: the logical worker instance identifier visible in the control plane; should be as stable as possible
- `session_id`: the WS session identifier recreated on each process start; not fixed
- `runtime.started_at` and `pid`: current process information; changes every restart

The correct goals are:

- The same logical worker continues using the same `instance_id` after restart
- Each startup gets a new `session_id`
- The control plane can still see this as a new process start, not the old process still alive

## 2. `instance_id` Resolution Order

`ControlPlaneReporterConfig.from_env()` resolves instance identity in the following order:

1. `ONESTEP_INSTANCE_ID`
2. `ONESTEP_REPLICA_KEY`
3. Local identity state in `ONESTEP_STATE_DIR`

Meaning:

- If `ONESTEP_INSTANCE_ID` is set, it always takes precedence
- If `ONESTEP_INSTANCE_ID` is not set but `ONESTEP_REPLICA_KEY` is set, `onestep` generates a deterministic UUIDv5 based on `service_name + environment + replica_key`
- If neither is set, `onestep` reads `instance_id` from `identity.json` in the local state directory; it creates a new one on first startup

## 3. How to Fix `instance_id`

Choose one of the following 3 approaches based on your scenario.

### 3.1 Approach 1: Persist `ONESTEP_STATE_DIR`

Applicable scenarios:

- Single-machine, single-process worker
- `systemd`, supervisor, long-running VM processes

How it works:

- `onestep` saves `identity.json` in the state directory
- After restart, it reuses the same `instance_id`
- `heartbeat_sequence` and `sync_sequence` also continue incrementing from this file

Default state directory:

- `~/.onestep/control-plane-state/<environment>/<service_name>`
- When `ONESTEP_REPLICA_KEY` is set, it becomes `~/.onestep/control-plane-state/<environment>/<service_name>/<replica_key>`

Example:

```bash
export ONESTEP_ENV=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_STATE_DIR=/var/lib/onestep/billing-sync
```

Note:

- This directory must persist across restarts
- Do not let two live workers share the same state directory
- If two live processes share a state directory, startup will fail immediately due to identity lock

This is the most recommended approach for single-worker scenarios.

### 3.2 Approach 2: Set `ONESTEP_REPLICA_KEY`

Applicable scenarios:

- Multiple worker replicas for the same service
- Kubernetes StatefulSet
- Fixed worker slots such as `worker-0`, `worker-1`

How it works:

- `onestep` generates a stable UUID from service name, environment, and replica key
- The same `replica_key` always maps to the same `instance_id`
- Different `replica_key` values produce different `instance_id` values

Example:

```bash
export ONESTEP_ENV=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_REPLICA_KEY=worker-0
```

StatefulSet example:

```bash
export ONESTEP_REPLICA_KEY="${HOSTNAME##*-}"
```

This approach requires the replica key itself to be stable. For example, `worker-0` should always represent the same logical replica.

Do not use random pod names that change on every rollout as a stable replica key, unless you have injected a stable logical identifier yourself.

This is the most recommended approach for multi-replica scenarios.

### 3.3 Approach 3: Explicitly Set `ONESTEP_INSTANCE_ID`

Applicable scenarios:

- Testing
- Manual migration
- You need to pin a specific UUID manually

Example:

```bash
export ONESTEP_INSTANCE_ID=8f9f0d7c-4b4a-4a58-8a6f-52d6735f44df
```

Note:

- This is a hard override
- All processes using the same `ONESTEP_INSTANCE_ID` will claim to be the same logical instance
- Do not assign the same explicit UUID to multiple live replicas

In production, multi-replica deployments should prefer `ONESTEP_REPLICA_KEY` rather than manually assigning fixed UUIDs to each replica.

## 4. Configuration by Deployment Pattern

### 4.1 Single-Machine, Single Worker

Recommended configuration:

- Set `ONESTEP_ENV`
- Set `ONESTEP_SERVICE_NAME`
- Set a persistent `ONESTEP_STATE_DIR`

Example:

```bash
export ONESTEP_ENV=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_SERVICE_DESCRIPTION="Synchronizes billing data into the warehouse"
export ONESTEP_STATE_DIR=/var/lib/onestep/billing-sync
```

Result:

- `instance_id` remains unchanged after restart
- The control plane still sees the same logical instance

`ONESTEP_SERVICE_DESCRIPTION` is an optional service-level description displayed in the control plane's service catalog; it is independent of task-level `tasks[].description`.

### 4.2 Multiple Workers on the Same Machine

Recommended configuration:

- Keep `ONESTEP_SERVICE_NAME` consistent
- Give each process a different `ONESTEP_REPLICA_KEY`
- Or give each process a different `ONESTEP_STATE_DIR`

Example:

```bash
export ONESTEP_ENV=prod
export ONESTEP_SERVICE_NAME=billing-sync
export ONESTEP_REPLICA_KEY=worker-2
export ONESTEP_STATE_DIR=/var/lib/onestep/billing-sync/worker-2
```

Result:

- Each worker has its own stable logical instance identity
- No lock conflicts

### 4.3 Kubernetes StatefulSet

Recommended configuration:

- Use the stable ordinal as `ONESTEP_REPLICA_KEY`
- Keep `ONESTEP_SERVICE_NAME` and `ONESTEP_ENV` stable

If pod names are `billing-sync-0`, `billing-sync-1`, `billing-sync-2`, extract the ordinal in the startup script and pass it to `ONESTEP_REPLICA_KEY`.

Example:

```bash
export ONESTEP_REPLICA_KEY="${HOSTNAME##*-}"
```

This maps `0`, `1`, `2` to fixed logical replicas.

### 4.4 Kubernetes Deployment

Recommended configuration:

- Do not rely on random pod names to pin instance identity
- Use `ONESTEP_REPLICA_KEY` only when you can assign stable logical replica numbers yourself
- If each pod replacement should be treated as a new logical worker, accept a new `instance_id`

If you want a logical worker to retain the same `instance_id` across restarts in a Deployment, you need to provide your own stable replica allocation mechanism rather than relying on the platform's auto-generated temporary names.

## 5. How to Verify the Identity Is Fixed

Start a worker once, then check the state file:

```bash
cat ~/.onestep/control-plane-state/prod/billing-sync/identity.json
```

Key fields:

- `instance_id`
- `heartbeat_sequence`
- `sync_sequence`
- `created_at`
- `updated_at`

After restart you should see:

- `instance_id` unchanged
- `heartbeat_sequence` continues increasing
- `sync_sequence` continues increasing
- The control plane shows a new session but still the same logical instance

## 6. Common Questions

### 6.1 Why did `instance_id` change after restart?

Common causes:

- `ONESTEP_STATE_DIR` is not persisted
- `ONESTEP_SERVICE_NAME`, `ONESTEP_ENV`, or `ONESTEP_REPLICA_KEY` changed
- Running in a temporary filesystem without explicitly overriding the identity source

Solutions:

- Use a persistent `ONESTEP_STATE_DIR`
- Or set a stable `ONESTEP_REPLICA_KEY`
- Or explicitly set `ONESTEP_INSTANCE_ID`

### 6.2 Why does startup fail with an identity lock error?

Cause:

- Two live processes competing for the same state directory

Solution:

- Use different `ONESTEP_STATE_DIR` for each process
- Or use different `ONESTEP_REPLICA_KEY` for each process

### 6.3 Why does the control plane show multiple logical instances?

Causes:

- Worker restarts without a stable identity source
- Or different replicas actually received inconsistent identity inputs

Debugging order:

- Check `ONESTEP_INSTANCE_ID`
- Check `ONESTEP_REPLICA_KEY`
- Check `ONESTEP_STATE_DIR`
- Check whether the state directory is actually persisted
- Check whether the same logical replica always uses the same set of inputs

## 7. Practical Recommendations

Follow these rules directly:

- Single-machine, single worker: fix `ONESTEP_STATE_DIR`
- Multi-replica workers: fix `ONESTEP_REPLICA_KEY`
- Must specify a UUID: set `ONESTEP_INSTANCE_ID`
- Do not let two live workers share the same state directory
- Do not let multiple live replicas share the same explicit `ONESTEP_INSTANCE_ID`

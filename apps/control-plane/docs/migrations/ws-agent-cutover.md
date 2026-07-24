# WS Agent Cutover

This rollout retires the historical HTTP agent ingestion path and moves OneStep
agents to the WS-only control plane session at `GET /api/v1/agents/ws`.

## Scope

- Agent telemetry and control share one authenticated WS session
- Query, auth, and health endpoints remain HTTP
- Agent commands are persisted in `agent_commands`
- Agent sessions are persisted in `agent_sessions`

## Prerequisites

- Deploy `onestep-control-plane` with Alembic head applied
- Configure `ONESTEP_CP_INGEST_TOKENS`
- Upgrade `onestep` agents to a build that includes `control_plane_ws.py`
- Ensure `websockets` is available in the agent environment

## Agent Configuration

Set these env vars on the agent side:

- `ONESTEP_CONTROL_PLANE_URL`
- `ONESTEP_CONTROL_PLANE_TOKEN`
- `ONESTEP_CONTROL_PLANE_ENVIRONMENT`

The reporter now connects to `/api/v1/agents/ws` automatically. No HTTP agent
ingestion fallback remains.

## Rollout Steps

1. Upgrade the control plane and run `alembic upgrade head`.
2. Verify `/readyz` is healthy and `ingestion_auth_configured` is correct.
3. Start one upgraded agent and confirm:
   - `hello` creates an active `agent_session`
   - `sync` and `heartbeat` land in the service dashboard
   - `GET /api/v1/services/{service_name}/commands` shows command lifecycle data
4. Dispatch `ping` from the UI or command API and confirm:
   - `command_ack` moves the command to `accepted`
   - `command_result` stores `status`, `result`, and `duration_ms`
5. Roll the remaining agents.

## Operational Notes

- Control plane startup reconciles any persisted `active` sessions to `disconnected`
  before new agents reconnect, so stale sessions from a prior API process do not
  remain command targets.
- Only unacked commands are redelivered after reconnect.
- Commands that are never acked expire in the control plane.
- Accepted commands that never produce a terminal result reconcile to `timeout`.
- The canonical WS protocol lives in `docs/protocols/agent-ws-protocol.md`.

## Local Smoke

If both repos are checked out side by side, run:

```bash
cd ../onestep
./scripts/run-control-plane-smoke.sh
```

The smoke script starts the local control plane, runs the OneStep demo agent,
dispatches a `ping`, and waits for a successful command result.

# onestep-worker-agent

Execution host agent for OneStep Control Plane deployments.

## Local Start

```bash
export ONESTEP_PLANE_URL=http://localhost:8000
export ONESTEP_AGENT_REGISTRATION_TOKEN=dev-token
export ONESTEP_WORKER_AGENT_DIR=.onestep-worker-agent
export ONESTEP_WORKER_AGENT_MAX_CONCURRENCY=2
onestep-worker-agent start
```

The agent registers once, stores its identity under `ONESTEP_WORKER_AGENT_DIR`,
connects to the control plane, and runs assigned workflow packages with
`onestep check worker.yaml` followed by `onestep run worker.yaml`.

Runtime state is also stored under `ONESTEP_WORKER_AGENT_DIR`:

- `identity.json`: stable worker-agent identity and connection credential.
- `deployments.json`: locally running deployments, including runtime identity,
  package directory, entrypoint, environment, and child process PID.

On restart, the agent restores deployments whose recorded PID is still alive,
reports them in the next control-plane hello/heartbeat, and can stop them by
PID. Stale records whose PID no longer exists are removed during startup.

## End-to-End Smoke

With a sibling `onestep-control-plane` checkout, run a real local deployment
smoke:

```bash
uv run python scripts/run_smoke.py
```

The smoke starts a temporary SQLite-backed control plane, starts this worker
agent, uploads a minimal workflow package, creates a deployment, waits for the
`running` event, stops it, and waits for `stopped`.

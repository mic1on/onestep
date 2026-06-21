# onestep-worker-agent

Execution host agent for OneStep Control Plane deployments.

## Setup

```bash
onestep-agent setup
```

If the config file does not exist, `setup` prompts for the Control Plane URL,
registration token, agent name, worker-agent directory, and max concurrency.
It writes:

```text
~/.onestep/worker-agent/config.json
```

For non-interactive deployment:

```bash
onestep-agent setup \
  --plane-url http://localhost:8000 \
  --registration-token dev-token \
  --name worker-agent \
  --max-concurrency 2 \
  --no-start
```

Then start the agent in the background:

```bash
onestep-agent start
```

For foreground debugging, use `run` instead:

```bash
onestep-agent run
```

`run` keeps the control loop attached to the current terminal. `start` launches
the same control loop in the background and writes logs to
`<worker-agent-dir>/agent.log`.

`onestep-worker-agent` remains available as a compatibility alias.

Use `--config-dir <dir>` with either command to store/read config elsewhere.
Environment variables still override config-file values:

- `ONESTEP_PLANE_URL`
- `ONESTEP_AGENT_REGISTRATION_TOKEN`
- `ONESTEP_WORKER_AGENT_DIR`
- `ONESTEP_WORKER_AGENT_NAME`
- `ONESTEP_WORKER_AGENT_MAX_CONCURRENCY`

The agent registers once, stores its identity under the worker-agent directory,
connects to the control plane, and runs assigned workflow packages with
`onestep check worker.yaml` followed by `onestep run worker.yaml`.

Runtime state is also stored under the worker-agent directory:

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

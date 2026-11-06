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

## Development

From the monorepo root:

```bash
uv sync --project apps/work-agent --frozen --extra dev
uv run --project apps/work-agent pytest apps/work-agent/tests
uv run --project apps/work-agent ruff check \
  apps/work-agent/src apps/work-agent/tests apps/work-agent/scripts
```

The worker-agent project keeps its own `pyproject.toml` and `uv.lock`; it is not
part of the root uv workspace.

## End-to-End Smoke

Run a real local deployment smoke against `apps/control-plane`:

```bash
uv run --project apps/work-agent python apps/work-agent/scripts/run_smoke.py
```

Pass `--control-plane-dir <path>` only when testing against another checkout.

The smoke starts a temporary SQLite-backed control plane, starts this worker
agent, uploads a minimal workflow package, creates a deployment, waits for the
`running` event, stops it, and waits for `stopped`.

## Release

The root `Worker Agent` workflow tests and builds changes under
`apps/work-agent`. An annotated `worker-agent-vX.Y.Z` tag publishes the matching
package version after the PyPI Trusted Publisher is configured for
`mic1on/onestep` and `.github/workflows/work-agent.yml`. The workflow can also
be run manually without publishing.

### Command and process recovery

The agent records each command under `work_dir/commands/` before performing it and
persists its terminal result before sending it. Repeated command IDs return that
result; different commands for one deployment execute serially. Starting an
already running deployment returns its existing runtime ID. Only restart replaces
the process. Keep this directory together with deployment state: deleting command
history permits old commands to execute again. Records are loaded individually,
so retained history does not grow the agent's in-memory working set.

Agents advertise `agent.command_recovery`. The control plane redelivers accepted
commands only to agents with that capability. An interrupted preparation ends as
`cancelled`; a start whose matching runtime survived is recovered as `succeeded`.
The agent persists a worker PID before opening its launch gate, so a crash before
that write cannot start an untracked business worker. Disconnect cancellation kills
and waits for installation/check processes; on POSIX, preparation process groups
also terminate if the agent itself exits abruptly.

Exited workers release their slot and log handle. Exit events remain in
`work_dir/exit-events.json` until the control plane acknowledges their committed
receipt, including across reconnects and agent restarts. Recovered PIDs are checked
on heartbeat. A delayed start result cannot override an already recorded exit for
the same runtime.

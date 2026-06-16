# OneStep Worker Agent Design

## Summary

Add `onestep-worker-agent` as a long-lived execution host controlled by
`onestep-control-plane`.

The agent is installed as a local CLI process or started as a container. On
startup it registers itself with the control plane, keeps a control connection
open, receives deployment commands, downloads control-plane-hosted workflow
packages, and runs each deployment as a local `onestep` subprocess.

The first implementation should use production-shaped identities and state
models, but keep execution narrow:

- `onestep-control-plane` owns workflow orchestration, package storage,
  deployment assignment, agent registry, state, audit, and control commands.
- `onestep-worker-agent` owns host-level execution and subprocess supervision.
- `onestep` remains the runtime library that executes sources, handlers, sinks,
  and existing runtime reporter behavior.
- A workflow compiles into one package and one deployment. The MVP does not
  split workflow nodes across agents.

## Goals

- Let a host join the control plane automatically after a CLI or container
  startup.
- Let the control plane decide which workflow package should run on which
  worker agent.
- Support multiple concurrent deployments per worker agent through fixed slot
  capacity.
- Keep the first execution mode simple: local subprocesses running
  `onestep check worker.yaml` and `onestep run worker.yaml`.
- Keep identities explicit so host-level state and runtime-level state do not
  collapse into one concept.
- Preserve the current `onestep` runtime model instead of moving scheduler or
  orchestration concerns into the runtime package.

## Non-Goals

- No `onestep-web` dependency or separate web-side orchestration surface.
- No task-level distributed DAG placement in the MVP.
- No Docker execution mode in the MVP.
- No CPU, memory, or resource-label scheduling in the MVP.
- No automatic restart policy beyond explicit manual restart commands.
- No external artifact backend in the MVP.
- No hot update of a running workflow.

## System Boundaries

### Repository Boundary

`onestep-worker-agent` should live in its own repository and publish as its own
CLI/container artifact. It is a runtime host with different dependencies,
release cadence, permissions, and operational lifecycle from
`onestep-control-plane`.

The control-plane repository should only contain the server-side protocol,
registry, package, deployment, and command implementation. It should not vendor
the worker-agent CLI code.

### `onestep-control-plane`

The control plane is the only orchestration surface.

It owns:

- workflow definition and compilation
- workflow package upload and storage
- worker agent registry
- worker agent sessions and heartbeats
- deployment creation and assignment
- desired state for deployments
- observed state from agents
- command audit and lifecycle
- package download authorization

It stores "what should be running" and compares that desired state with what
agents report.

### `onestep-worker-agent`

The worker agent is a long-lived execution host.

It owns:

- first-time registration with the control plane
- persistent host identity
- a control connection to the control plane
- local slot accounting
- package download, checksum validation, and extraction
- subprocess supervision for `onestep` runtime processes
- local deployment state recovery after reconnect or restart
- host-level state and deployment event reporting

It does not own workflow design, global scheduling, UI state, or durable
deployment truth.

### `onestep`

`onestep` remains the runtime package.

It owns:

- `OneStepApp`
- YAML app loading
- source, handler, sink execution
- task lifecycle
- existing control-plane runtime reporter behavior

The worker agent may inject environment variables for reporter configuration and
runtime identity, but it should not reimplement task execution.

## Identity Model

Use separate identities for host, connection, deployment, and runtime:

- `worker_agent_id`: stable identity for a host that can accept deployments.
- `worker_agent_session_id`: identity for one active control connection.
- `deployment_id`: a control-plane intent to run one workflow package.
- `runtime_instance_id`: identity for the concrete `onestep` runtime process
  started for a deployment.

This distinction matters because an agent can be online with no deployments,
can run multiple deployments, can reconnect while child processes continue, and
can survive a runtime crash.

## Registration

The agent starts with:

- control plane URL
- registration token
- working directory
- `max_concurrent_deployments`
- optional display name
- optional labels

On first startup the agent calls a registration endpoint and sends:

- agent version
- Python version
- installed `onestep` version, if available
- execution mode: `subprocess`
- maximum concurrent deployments
- platform information
- optional labels

The control plane validates the registration token, creates a
`worker_agent_id`, and returns:

- worker agent identity
- agent-specific connection credential
- default heartbeat interval
- accepted capabilities

The agent persists the returned identity and credential in a local state file.
Later restarts reuse the persisted identity and do not reuse the registration
token unless the local state is intentionally removed or re-bound.

## Control Connection

The worker agent uses a long-lived control connection for host-level control and
heartbeat traffic. It may follow the existing runtime Agent WS envelope style,
but it is a separate protocol surface from the current `onestep` runtime
reporter session.

The worker-agent connection reports host state:

- current session
- configured slots
- used slots
- running deployments
- agent version
- capabilities
- recent host-level errors

The runtime reporter reports runtime and task state for a specific
`runtime_instance_id`.

## Workflow Package Model

The control plane stores immutable workflow package versions.

A package contains:

- `worker.yaml`
- Python source code
- dependency declarations, such as `requirements.txt` or `pyproject.toml`
- package metadata
- entrypoint metadata
- checksum
- creator and creation time

The MVP uses control-plane-hosted package storage. A worker agent receives a
deployment-scoped download credential, downloads the package from the control
plane, verifies the checksum, and extracts it into an isolated deployment
working directory.

## Deployment Model

A deployment records one intent to run one workflow package on one worker agent.

Core fields:

- `deployment_id`
- `workflow_id`
- `workflow_package_id`
- `worker_agent_id`
- `desired_status`
- `observed_status`
- `runtime_instance_id`
- `execution_mode`
- runtime parameters
- environment and credential references
- package checksum
- last error
- audit fields

The control plane owns desired state. The agent owns observed local state. The
control plane persists both and reconciles drift.

## Deployment State Machine

Recommended MVP observed states:

- `pending`
- `assigned`
- `preparing`
- `checking`
- `running`
- `stopping`
- `stopped`
- `failed`
- `cancelled`

State meanings:

- `pending`: deployment exists but has not been assigned.
- `assigned`: a worker agent has been selected.
- `preparing`: agent is creating the working directory, downloading, verifying,
  and extracting the package.
- `checking`: agent is running `onestep check worker.yaml`.
- `running`: agent has started `onestep run worker.yaml`.
- `stopping`: plane requested stop and agent is terminating the child process.
- `stopped`: stop completed or the process ended cleanly.
- `failed`: preparation, check, start, runtime, or stop failed.
- `cancelled`: deployment was cancelled before execution began.

Desired state should be represented separately, for example:

- `running`
- `stopped`

This separation lets the control plane reconcile after reconnects instead of
treating the most recent event as the source of truth.

## Agent Commands

The MVP command set is deliberately small:

- `start_deployment`
- `stop_deployment`
- `restart_deployment`
- `sync_agent_state`

Every command includes:

- `command_id`
- `deployment_id`, when applicable
- `timeout_s`
- `created_at`
- idempotency key

The agent first returns `command_ack`, then later returns `command_result`.

The agent rejects a command when:

- no slot is available
- the deployment already exists with incompatible package metadata
- the command references an unknown local deployment in a context that requires
  it
- package metadata or checksum preconditions are invalid
- the command kind is unsupported by the agent capability set

Repeated `start_deployment` is idempotent when the same deployment is already
running with the same package checksum and runtime parameters.

## Local Execution

Each deployment gets:

- one slot
- one isolated working directory
- one package extraction directory
- one subprocess
- one local deployment state record

Execution flow:

1. Receive `start_deployment`.
2. Check slot capacity.
3. Create the deployment working directory.
4. Download the package.
5. Verify checksum.
6. Extract the package.
7. Inject runtime environment variables.
8. Run `onestep check worker.yaml`.
9. Start `onestep run worker.yaml`.
10. Track PID, stdout, stderr, exit code, and stop reason.
11. Report deployment events to the control plane.

Environment injection should include:

- `ONESTEP_DEPLOYMENT_ID`
- `ONESTEP_WORKER_AGENT_ID`
- `ONESTEP_RUNTIME_INSTANCE_ID`
- control-plane reporter settings required by the runtime

The MVP does not install dependencies implicitly. A package must either be
self-contained for the current agent environment or rely on dependencies that
are already installed on that host. Dependency installation can be added once
the package format defines how reproducible environments are produced.

## Stop And Restart

`stop_deployment` performs a graceful subprocess shutdown:

1. Mark deployment as `stopping`.
2. Send SIGTERM.
3. Wait for a configured grace period.
4. Send SIGKILL if the process is still alive.
5. Record exit status and stop reason.
6. Release the slot.
7. Report final state.

`restart_deployment` creates a new runtime attempt for the same deployment. The
deployment keeps its identity for the user's running intent, while the previous
`runtime_instance_id` is recorded as stopped and a new `runtime_instance_id` is
created for the restarted subprocess. This keeps restart auditability without
creating a second user-visible deployment.

## Reconnect And Reconcile

On reconnect, the worker agent sends a full local state snapshot:

- active local deployments
- package checksums
- runtime instance IDs
- PIDs
- last known observed states
- used and available slots

The control plane compares the snapshot against desired state:

- If desired is `running` and the process exists, continue and refresh state.
- If desired is `running` and no process exists, mark the deployment `failed`.
  The MVP does not auto-restart lost processes.
- If desired is `stopped` and a process exists, send stop.
- If the agent reports an unknown deployment, mark it as orphaned and stop it
  unless an operator explicitly adopts it.

The MVP uses manual restart policy. It should store a future `restart_policy`
field, but only support `manual` initially.

## Slot Management

Each worker agent has `max_concurrent_deployments`.

The control plane uses agent heartbeat data to select agents with free slots.
The agent also enforces its own slot limit and rejects excess start commands.

MVP scheduling only considers slot counts and online status. Resource labels,
CPU, memory, and affinity rules are future work.

## Testing

Control-plane tests:

- registration token validation
- worker agent creation
- worker agent session lifecycle
- heartbeat persistence
- package metadata and checksum handling
- deployment state transitions
- slot-based assignment
- command ack/result lifecycle
- idempotent duplicate commands

Worker-agent tests:

- local identity persistence
- registration and reconnect behavior
- package download and checksum validation
- isolated working directory creation
- subprocess start and stop
- SIGTERM to SIGKILL escalation
- slot rejection
- reconnect snapshot generation

Integration test:

1. Start the control plane.
2. Start one worker agent with two slots.
3. Upload a minimal package with `worker.yaml`.
4. Create a deployment.
5. Observe `assigned -> preparing -> checking -> running`.
6. Stop the deployment.
7. Observe `stopping -> stopped`.
8. Verify runtime reporter behavior is not broken.

## Implementation Sequence

1. Freeze the worker-agent protocol and schema names.
2. Add control-plane registry, package, deployment, and command models.
3. Add control-plane registration, heartbeat, package, and deployment APIs.
4. Implement worker-agent CLI with registration, identity persistence, and
   heartbeat.
5. Add control connection commands and command ack/result handling.
6. Add local subprocess supervisor.
7. Add package download, checksum verification, and extraction.
8. Add reconcile behavior on reconnect.
9. Add end-to-end integration tests.
10. Revisit Docker execution, labels, resource-aware scheduling, and restart
    policy after the subprocess MVP is stable.

## Open Decisions Locked For MVP

- Authentication uses startup plane URL plus registration token for first bind.
- The control plane hosts workflow packages.
- Execution uses local subprocesses.
- Agents support fixed deployment concurrency.
- A workflow compiles into one package and one deployment.
- The design may reserve future fields for Docker and task-level placement, but
  implementation should not include those behaviors in the MVP.

# OneStep Agent / Control Plane Monorepo Collaboration Guide

## 1. Background

OneStep runtime, control-plane reporter, and control-plane server form a complete system for communication, control capabilities, protocol evolution, and integrated testing. The code lives in the same repository but maintains independent build and release boundaries:

```text
onestep/
├── src/onestep/                         # runtime
├── plugins/onestep-control-plane/       # reporter / WS client
└── apps/control-plane/                  # FastAPI + React + Electron + Docker
```

Agent and Control Plane communicate exclusively over WebSocket. Frontend queries, login, and health checks continue to use HTTP. The goal of same-repo organization is to allow protocol-related changes to be completed and validated in one PR, not to merge three release units into one package.

## 2. Component Responsibilities

### 2.1 Runtime and Reporter

`src/onestep/` and `plugins/onestep-control-plane/` are responsible for:

- WS transport
- `hello`, `heartbeat` and telemetry
- Command receipt, acknowledgement, and result return
- Local buffering, prioritization and backpressure
- Reconnection
- Runtime identity and task control state

### 2.2 Control Plane

`apps/control-plane/` is responsible for:

- WS access and authentication
- Agent session management
- Telemetry validation and storage
- Command creation, dispatch, and state transitions
- Query API, Web Console and Desktop

### 2.3 Boundary Principles

- Protocol must exist before implementation; no ad-hoc field agreements.
- Server must tolerate old Agents; Agents must use capability negotiation for new features.
- Reporter payload, event semantics, identity, or remote control behavior changes must check both sides in the same PR.
- Same-repo does not mean same-release. Compatibility must still support independent upgrades and rollbacks.

## 3. Protocol Governance

The single source of truth for the Agent-Control Plane WS protocol is:

`apps/control-plane/docs/protocols/agent-ws-protocol.md`

The protocol must at minimum define:

- WS routing and authentication
- Message types and fields
- Idempotency rules
- Error semantics and reconnection rules
- Session and command lifecycle
- `protocol_version` and capabilities
- Runtime status fields such as `heartbeat.health.task_controls`

Protocol changes should prefer compatible extensions. When removing fields, renaming fields, or changing field semantics, provide upgrade order, compatibility window, and rollback instructions.

## 4. Standard Development Sequence

### Phase 1: Protocol and Compatibility

First update the protocol document, clarifying message direction, fields, idempotency keys, old-version behavior, and capabilities.

Completion criteria:

- Old and new Agent behavior is clear
- Server acceptance scope is clear
- Release and rollback order is clear

### Phase 2: Server Contract

Update `apps/control-plane/backend/` schema, persistence, and API behavior, and use a fake client to verify handshake, telemetry, and command lifecycle.

### Phase 3: Runtime and Reporter

Update `src/onestep/` or `plugins/onestep-control-plane/`, covering connection, reconnection, reporting, command ack/result, and capability negotiation.

### Phase 4: Same-Repo Contract Validation

`.github/workflows/control-plane-contract.yml` must use the current checkout's runtime and reporter, not just the published PyPI version. It must at minimum validate:

- Reporter and WS client unit tests
- Server handshake and telemetry schema
- E2E ingestion/control workflow
- Resource catalog and topology payload

### Phase 5: Independent Release

When a release is needed, follow this order:

1. Release compatible core and reporter packages.
2. Update control-plane dependency lower bounds and `apps/control-plane/uv.lock`.
3. Build and publish control-plane image.
4. Run smoke tests with both old and new Agents.

## 5. PR Rules

Protocol-related needs use one PR. The PR description must list:

- Protocol changes and compatibility
- Affected components
- Package/image release order
- Test and smoke evidence
- Work not within this PR's scope

Implementation can be split into multiple clear commits, for example:

```text
docs: define service description payload
feat: report service description
feat: store and expose service description
test: cover reporter and plane contract
```

Not allowed:

- Modifying only the reporter or server side and skipping contract checks
- Ad-hoc field naming or error semantics during integration testing
- Using local path dependencies in place of formal released versions and forgetting to restore
- Abandoning version compatibility and capability negotiation because the code is in the same repo

## 6. Test Strategy

### Runtime / Reporter

Must cover connection, `hello`, heartbeat, telemetry, command ack/result, reconnection backoff, buffer flush, and identity persistence.

### Control Plane

Must cover WS authentication, session, telemetry ingestion, command state transitions, message idempotency, disconnection, and database migrations.

### E2E

Minimum validation loop:

1. Start control plane.
2. Start an Agent with reporter enabled.
3. Confirm session is online and sync completes.
4. Dispatch a supported command.
5. Receive `command_ack` and `command_result`.
6. Verify telemetry persistence and UI query results.

## 7. Local Development

Operate the two Python projects from the repository root:

```bash
# Runtime / plugins, Python 3.9+
uv sync --all-packages --extra test

# Control plane, Python 3.11+
uv sync --project apps/control-plane --extra dev

# Control plane frontend
pnpm --dir apps/control-plane install --frozen-lockfile
```

`apps/control-plane` is not part of the root uv workspace. The server and reporter currently use the same Python distribution name and have different Python version ranges; forcing a workspace merge would break Python 3.9 core testing.

## 8. CI Boundaries

```text
src/** or plugins/onestep-control-plane/**
  ├── core/plugin CI
  └── control-plane contract CI

apps/control-plane/backend/**
  ├── control-plane full CI
  └── control-plane contract CI

apps/control-plane/frontend/** or desktop/**
  └── control-plane full CI
```

The control-plane image name remains `ghcr.io/mic1on/onestep-control-plane`. Moving into the directory does not change the user's pull address, Compose environment variables, or database migration approach.

## 9. Completion Criteria

- Protocol documentation and implementation are consistent.
- Current checkout runtime, reporter, and server contract tests pass.
- Both `uv.lock` files pass frozen checks.
- Control-plane backend, frontend, E2E, Docker build, and smoke pass.
- Release order, compatibility window, and rollback approach are documented in the PR.
- No new cross-component implicit path dependencies.

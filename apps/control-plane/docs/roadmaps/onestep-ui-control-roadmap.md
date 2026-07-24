# OneStep UI Control Roadmap

## Status

This document describes the recommended roadmap for evolving the current
`onestep-control-plane` UI from a monitoring-oriented console into a safer and
more complete operator control surface for OneStep runtimes.

The roadmap is based on the current code in this repository as of 2026-03-18.

## Current State

The shipped control surface is intentionally narrow:

- The instance detail page can dispatch instance-scoped commands.
- The currently exposed commands are `ping`, `sync_now`, `flush_metrics`,
  `flush_events`, and `shutdown`.
- The service detail page can inspect command history and session history, but
  it does not provide a service-level control entry point.
- Commands are persisted in `agent_commands`, sessions are persisted in
  `agent_sessions`, and command lifecycle state is visible in the UI.
- Agent capabilities are negotiated during WS `hello` and surfaced to the UI as
  `accepted_capabilities`.
- Browser-side command and session views are refreshed by polling every 5
  seconds rather than by a push channel.

## Key Gaps

- No service-level fanout control for a whole service or a selected subset of
  instances.
- No capability-aware enablement in the UI. A connected instance may expose only
  some command capabilities, but the UI does not gate actions per capability.
- No offline queueing UX, even though the backend already supports pending
  commands and reconnect-time redelivery.
- No runtime maintenance primitives beyond `shutdown`.
- No task- or workflow-level controls such as pause, resume, drain, replay, or
  dead-letter recovery.
- No audit fields such as actor, reason, or source surface for command
  dispatches.
- No granular authorization model beyond the shared console login.
- No push-based browser channel for near-real-time command state changes.

## Guiding Principles

- Only expose actions the target agent session has explicitly advertised through
  `accepted_capabilities`.
- Every control action must remain target-scoped and observable through the
  normal command lifecycle.
- Risky actions must require explicit user confirmation and capture operator
  intent.
- Bulk operations should create one persisted command per target instance rather
  than a synthetic black-box job.
- Any new control primitive that requires runtime support must be coordinated
  with the `onestep` runtime repository and the WS protocol.

## P0 Roadmap

P0 turns the existing command surface into something operators can safely use in
production without changing the fundamental control model.

### P0.1 Capability-Aware Instance Controls

**Outcome**

The instance page should only enable controls that the active session has
explicitly negotiated. Unsupported actions should be visible but disabled with a
clear reason.

**Why this is first**

The backend already exposes `accepted_capabilities`, so the missing piece is
mostly UI correctness and operator safety.

**Primary files**

- Modify: `frontend/src/pages/instance-detail/InstanceDetailPage.tsx`
- Modify: `frontend/src/features/commands/components/CommandQuickActions.tsx`
- Modify: `frontend/src/features/commands/components/CommandFeed.tsx`
- Modify: `frontend/src/lib/i18n.ts`
- Modify: `frontend/src/lib/api/types.ts`

**Implementation notes**

- Pass `accepted_capabilities` into the quick action component.
- Map each command kind to the expected WS capability name.
- Disable unsupported actions instead of only checking for `active_session`.
- Show a concise inline explanation for disabled commands.
- Keep `shutdown` behind a stronger confirmation path than read-only commands.

**Acceptance criteria**

- A connected instance that advertises only `command.sync_now` cannot trigger
  `shutdown`, `flush_metrics`, `flush_events`, or `ping` from the UI.
- The operator can tell whether a disabled action is blocked by lack of session
  or lack of capability.
- The command history remains unchanged except for clearer state labeling.

### P0.2 Service-Level Fanout Control

**Outcome**

The service detail page should support dispatching an allowed command to a set
of target instances, starting with all online instances or an explicit selected
subset.

**Why this is first**

Operators currently have to click into each instance one by one. That is viable
for debugging but not for service operations.

**Primary files**

- Modify: `backend/src/onestep_control_plane_api/api/routers/commands.py`
- Modify: `backend/src/onestep_control_plane_api/api/agent_command_service.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/query.py`
- Modify: `backend/tests/test_query_api.py`
- Modify: `backend/tests/test_agent_ws.py`
- Modify: `frontend/src/pages/service-detail/ServiceDetailPage.tsx`
- Create: `frontend/src/features/commands/components/ServiceCommandFanout.tsx`
- Modify: `frontend/src/features/commands/queries.ts`
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/i18n.ts`

**Implementation notes**

- Add a service-scoped command creation endpoint that resolves target instances
  server-side.
- Persist one `agent_commands` row per target instance.
- Return a structured summary that separates dispatched, queued, skipped, and
  rejected targets.
- Restrict the initial fanout surface to non-destructive commands:
  `sync_now`, `flush_metrics`, `flush_events`, and optionally `ping`.
- Keep `shutdown` out of the first fanout release unless the UX includes strong
  warnings and explicit target review.

**Acceptance criteria**

- An operator can dispatch `sync_now` to all online instances for a service in
  one action.
- The resulting command history still appears as per-instance command rows.
- Offline instances are handled explicitly as either queued or skipped based on
  the chosen mode.
- The UI shows a summary of which targets received which outcome.

### P0.3 Command Audit Metadata

**Outcome**

Every command should capture who sent it, why it was sent, and from which UI
surface it originated.

**Why this is first**

A control plane without operator attribution becomes hard to trust once commands
move beyond local debugging.

**Primary files**

- Modify: `backend/src/onestep_control_plane_api/db/models.py`
- Create: `backend/alembic/versions/<new_revision>_add_agent_command_audit_fields.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/commands.py`
- Modify: `backend/src/onestep_control_plane_api/api/agent_command_service.py`
- Modify: `backend/src/onestep_control_plane_api/api/query_support.py`
- Modify: `backend/tests/test_migrations.py`
- Modify: `backend/tests/test_query_api.py`
- Modify: `frontend/src/pages/instance-detail/InstanceDetailPage.tsx`
- Create: `frontend/src/features/commands/components/CommandReasonDialog.tsx`
- Modify: `frontend/src/lib/api/client.ts`
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/i18n.ts`

**Implementation notes**

- Add fields such as `created_by`, `reason`, and `source_surface`.
- Populate `created_by` from the authenticated console session.
- Require `reason` for destructive commands and for bulk fanout.
- Show the audit metadata in command detail cards and service-level command feed.

**Acceptance criteria**

- A `shutdown` command cannot be submitted without a reason.
- The resulting command record includes the console username.
- Operators can inspect why a command was sent after the fact.

## P1 Roadmap

P1 expands the control model beyond the initial five commands while still
keeping the unit of control at the runtime instance.

### P1.1 Offline Queueing and Reconnect Delivery UX

**Outcome**

Operators should be able to queue eligible commands for disconnected instances
and rely on reconnect-time delivery, rather than being blocked by a disabled UI.

**Primary files**

- Modify: `frontend/src/pages/instance-detail/InstanceDetailPage.tsx`
- Modify: `frontend/src/pages/service-detail/ServiceDetailPage.tsx`
- Modify: `frontend/src/features/commands/components/CommandQuickActions.tsx`
- Modify: `frontend/src/features/commands/components/ServiceCommandFanout.tsx`
- Modify: `frontend/src/lib/i18n.ts`
- Modify: `backend/src/onestep_control_plane_api/api/agent_command_service.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/commands.py`
- Modify: `backend/tests/test_agent_ws.py`
- Modify: `backend/tests/test_query_api.py`

**Implementation notes**

- Reuse the existing `pending` plus reconnect-time redelivery behavior.
- Add an explicit operator choice between `dispatch_now_only` and
  `queue_until_reconnect`.
- Make the UI honest about what the backend will do for offline targets.

**Acceptance criteria**

- A disconnected instance can receive a queued `sync_now` that dispatches after
  the next successful WS session.
- The UI distinguishes `queued`, `dispatched`, `expired`, and `timeout` states.

### P1.2 Runtime Maintenance Primitives: `restart` and `drain`

**Outcome**

The control plane should support operationally useful lifecycle commands beyond
`shutdown`.

**Cross-repo dependency**

This item requires coordinated changes in the OneStep runtime repo. The control
plane can add protocol and UI support here, but the runtime must implement the
behavior.

**Primary files in this repo**

- Modify: `docs/protocols/agent-ws-protocol.md`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/agent_ws.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/commands.py`
- Modify: `backend/src/onestep_control_plane_api/api/agent_command_service.py`
- Modify: `backend/tests/test_agent_ws.py`
- Modify: `frontend/src/features/commands/components/CommandQuickActions.tsx`
- Modify: `frontend/src/features/commands/components/ServiceCommandFanout.tsx`
- Modify: `frontend/src/lib/api/types.ts`
- Modify: `frontend/src/lib/i18n.ts`

**Implementation notes**

- `restart` is the simplest next primitive if the runtime can shut down and rely
  on an external supervisor to bring it back.
- `drain` is more valuable operationally, but it only makes sense if the runtime
  can stop taking new work while allowing in-flight work to finish.
- Do not expose these controls until the runtime can report deterministic result
  payloads for success and failure.

**Acceptance criteria**

- The protocol advertises `command.restart` and `command.drain` only when the
  runtime truly supports them.
- The UI does not surface speculative controls for older agents.
- Command results include enough data to tell whether the maintenance action was
  complete or partial.

### P1.3 Browser Push for Command and Session State

**Outcome**

The UI should move from periodic polling to a push channel for near-real-time
operator feedback.

**Primary files**

- Modify: `backend/src/onestep_control_plane_api/main.py`
- Create: `backend/src/onestep_control_plane_api/api/routers/ui_ws.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `frontend/src/app/providers.tsx`
- Modify: `frontend/src/features/commands/queries.ts`
- Create: `frontend/src/features/commands/useCommandStream.ts`
- Modify: `frontend/src/lib/api/types.ts`

**Implementation notes**

- SSE is probably simpler than a second browser WS, but either is acceptable.
- Push should augment command and session updates first, not replace the query
  API for all dashboard data.

**Acceptance criteria**

- A command that moves from `dispatched` to `accepted` to `succeeded` appears in
  the browser without waiting for the next poll interval.
- If the push channel drops, the UI falls back cleanly to periodic refetch.

## P2 Roadmap

P2 is where the product starts to become a true operations console rather than a
thin remote-command surface.

### P2.1 Task- and Workflow-Level Controls

**Outcome**

Support runtime control at the task graph level instead of only at the instance
level.

**Examples**

- Pause or resume intake for a task.
- Replay dead-lettered work.
- Trigger targeted task retry or skip behavior.
- Drain only a subset of task executors.

**Dependencies**

- New runtime primitives in the `onestep` repo.
- Protocol additions in `docs/protocols/agent-ws-protocol.md`.
- New query surfaces to expose task-level control state.

### P2.2 Role-Based Authorization and Approvals

**Outcome**

Replace the current shared-console trust model with explicit privileges.

**Examples**

- Read-only observer role.
- Operator role for safe commands.
- Elevated role or approval workflow for `shutdown`, `restart`, and future
  destructive controls.

**Likely files**

- Modify: `backend/src/onestep_control_plane_api/api/security.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/auth.py`
- Modify: `backend/src/onestep_control_plane_api/api/routers/commands.py`
- Modify: `backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `frontend/src/features/auth/*`
- Modify: `frontend/src/pages/instance-detail/InstanceDetailPage.tsx`
- Modify: `frontend/src/pages/service-detail/ServiceDetailPage.tsx`

### P2.3 Topology and Runtime Configuration Mutation

**Outcome**

Allow operators to change runtime configuration from the control plane rather
than treating topology as read-only telemetry.

**Examples**

- Change task concurrency.
- Change retry policy.
- Toggle connector behavior.
- Update deployment-scoped runtime flags.

**Warning**

This should stay out of scope until there is a clear runtime-side configuration
model, validation path, rollback model, and ownership boundary.

## Recommended Delivery Order

1. Ship capability-aware instance controls.
2. Ship service-level fanout for safe commands.
3. Ship audit metadata and required reasons.
4. Expose offline queueing intentionally instead of hiding it.
5. Add runtime maintenance primitives after the runtime contract exists.
6. Add browser push once command volume makes polling ergonomically weak.
7. Start task-level control only after the runtime model is proven.

## File Map by Surface

### Existing control surfaces

- `frontend/src/pages/instance-detail/InstanceDetailPage.tsx`
- `frontend/src/features/commands/components/CommandQuickActions.tsx`
- `frontend/src/features/commands/components/CommandFeed.tsx`
- `frontend/src/features/commands/components/SessionList.tsx`
- `frontend/src/pages/service-detail/ServiceDetailPage.tsx`
- `frontend/src/features/commands/queries.ts`
- `frontend/src/lib/api/client.ts`
- `frontend/src/lib/api/types.ts`
- `backend/src/onestep_control_plane_api/api/routers/commands.py`
- `backend/src/onestep_control_plane_api/api/agent_command_service.py`
- `backend/src/onestep_control_plane_api/api/routers/agent_ws.py`
- `backend/src/onestep_control_plane_api/api/routers/query.py`
- `backend/src/onestep_control_plane_api/api/schemas.py`
- `backend/src/onestep_control_plane_api/db/models.py`

### New files likely to appear

- `docs/roadmaps/onestep-ui-control-roadmap.md`
- `frontend/src/features/commands/components/ServiceCommandFanout.tsx`
- `frontend/src/features/commands/components/CommandReasonDialog.tsx`
- `frontend/src/features/commands/useCommandStream.ts`
- `backend/src/onestep_control_plane_api/api/routers/ui_ws.py`
- `backend/alembic/versions/<new_revision>_add_agent_command_audit_fields.py`

## Exit Criteria for "Control Plane v1.5"

The UI can be considered meaningfully upgraded when all of the following are
true:

- Instance controls are capability-aware.
- Service-level fanout exists for safe commands.
- Command records include actor and reason.
- Operators can intentionally queue eligible commands for offline instances.
- The UI reflects command lifecycle changes quickly enough for live operations.

At that point, the next strategic question becomes whether to keep the product
as an instance-command console or evolve it into a richer workflow operations
platform.

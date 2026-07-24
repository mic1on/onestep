# Topology Flow Motion Design

Date: 2026-07-18
Status: Ready for user review
Owner: Codex

## Summary

The topology panel should feel more alive while a task is actively processing
work. Add a lightweight staged flow animation to the existing frontend topology
diagram: the source subtly signals data fetch, particles move through the first
connection, the handler node shows a small work pulse, particles move through the
second connection, and the sink subtly signals downstream write.

The selected design is frontend-only. It uses existing task state and
`throughputPerMin` to decide whether animation is active and how fast particles
move. It does not change the worker reporter payload, control-plane protocol,
database schema, or API response shape.

## Goals

- Make the topology diagram communicate "data fetched, handled, and written
  downstream" at a glance.
- Make higher throughput feel faster by shortening the flow animation duration.
- Keep the handler pulse subtle, with a very small scale/glow change rather than
  a large heartbeat.
- Stop motion for paused, offline, failed, idle, or zero-throughput tasks.
- Keep the current node layout, click-to-inspect behavior, and source/sink detail
  rendering intact.
- Preserve the narrow-screen vertical topology behavior.

## Non-Goals

- Do not add true per-stage runtime telemetry in this change.
- Do not modify reporter payload fields or task lifecycle event semantics.
- Do not replace the topology with React Flow or another graph library.
- Do not redesign the detail panel below the topology.
- Do not introduce user-configurable animation settings.

## Assumptions

- The first release can use a visual approximation driven by
  `task.viewStatus === "running"` and `task.throughputPerMin > 0`.
- The existing per-minute throughput is enough to express relative speed even
  though it does not distinguish fetch, handler, and sink stages.
- Users care more about quickly seeing whether work is flowing than about exact
  in-flight stage counts in this panel.
- Users with reduced-motion preferences should not see continuous particle
  motion.

## Chosen Approach

### Staged Flow Animation

When a task is running and has positive throughput:

1. The source node gets a brief, low-intensity fetch glow.
2. The source-to-handler connector shows moving particles and a soft sweep.
3. The handler node shows a subtle work pulse.
4. The handler-to-sink connector shows moving particles and a soft sweep.
5. The sink node gets a brief downstream-write glow.

The handler pulse should be intentionally restrained:

- scale around `1.015`, not a large bounce;
- glow around 5px and low opacity;
- no aggressive color flash;
- keep the existing CPU icon pulse only if it does not compound into a noisy
  effect.

### Speed Mapping

Derive an animation duration from `throughputPerMin`:

- low throughput: slower particles;
- medium throughput: moderate movement;
- high throughput: faster movement;
- clamp the result so the animation never becomes frantic or imperceptibly slow.

A practical first-pass range is roughly `0.75s` to `2.4s` per flow cycle. The
exact formula can live in a small local helper in `TopologyFlow.tsx`, for example
mapping throughput buckets to duration classes or a CSS variable.

### Inactive States

If the task is not actively flowing, render static connectors:

- `paused`, `offline`, `failed`, and `idle` do not animate;
- `running` with `throughputPerMin <= 0` does not animate;
- the existing status dot still reflects runtime state.

Use `prefers-reduced-motion: reduce` to disable continuous connector and node
animation while keeping the static diagram usable.

## Alternatives Considered

### Data Packet Lines Only

This would add moving particles on connectors without staged node responses. It
is the smallest visual change and easy to maintain, but it does not communicate
the full "fetch, process, write" story the user asked for.

### Progress Fill Tracks

This would make connectors look like progress bars. It can express completion,
but without true in-flight stage telemetry the progress value would be synthetic
and could be misleading.

### True Stage Event Telemetry

The worker could report fetch, handler, and sink commit events or counters, and
the plane could store/render them. This would be more accurate, but it touches
the reporter protocol and control-plane coordination boundary. It is not needed
for the first visual polish pass.

## Components And Data Flow

Primary file:

- `frontend/src/components/TopologyFlow.tsx`

Existing inputs:

- `task.viewStatus`
- `task.throughputPerMin`
- existing source, task, and sink labels/config

Implementation shape:

- derive `isFlowing` from running status and positive throughput;
- derive `flowDurationSeconds` from throughput;
- apply a CSS variable to the topology container;
- replace the plain connector line with an animated connector that still uses
  the existing responsive horizontal/vertical layout;
- add small active classes to source, handler, and sink nodes only when
  `isFlowing` is true;
- keep node click handlers and selected-node styling unchanged.

## Testing

Focused verification:

- Add or update frontend tests around `TopologyFlow` if an existing component
  test surface is available.
- At minimum, verify build/typecheck catches the JSX and CSS changes:
  `cd frontend && pnpm build`.
- Manually verify a running task with throughput:
  - particles move along both connectors;
  - handler pulse is subtle;
  - faster throughput feels faster than low throughput.
- Manually verify inactive states:
  - paused task is static;
  - idle or zero-throughput running task is static;
  - reduced-motion mode removes continuous motion.

## Scope Boundary

This change should remain a surgical frontend change to the topology panel. No
control-plane rebuild coordination is required unless product code outside the
frontend component is later changed. If future work adds real stage telemetry,
that would require coordination with the control-plane protocol and storage
rules before release.

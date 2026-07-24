# Paused Task Current-Time Metrics Design

Date: 2026-07-18
Status: Ready for user review
Owner: Codex

## Summary

Task detail metrics should prioritize the current-time truth when a task is
paused. If a task is paused and no new work is being fetched, the page should
make that obvious: the throughput card shows the current effective rate, and the
chart timeline is anchored to "now minus 15 minutes" through "now" instead of
stretching only the latest historical metric windows.

The selected approach keeps the backend reporter protocol unchanged. Runtime
workers continue to report only windows with activity. The frontend renders those
windows against a current-time 15 minute axis and uses task status to make paused
tasks read as inactive now.

## Problem

Today the task page mixes two meanings:

- The header status says the task is paused.
- The throughput card and chart can still show recent historical activity from
  before the pause.

This is technically explainable because metric windows remain inside the
rolling lookback until they age out. It is still misleading. A user who pauses a
task wants the first screen to answer: "is this task still consuming work now?"

## Goals

- Make paused task detail pages express the current state first.
- Show `0/min` as the effective current throughput when a task is paused.
- Keep the metric chart scoped to the current lookback interval ending at now.
- Let old pre-pause windows remain visible only in their actual position on the
  timeline, with empty or zero space after the pause.
- Avoid changing runtime telemetry payloads or requiring workers to emit empty
  metric windows.

## Non-Goals

- Do not change task pause/resume command semantics.
- Do not change metric ingestion, database schema, or reporter window rotation.
- Do not introduce new backend aggregation semantics unless frontend-only
  rendering cannot make the current-time view clear.
- Do not redesign the full task detail page.

## Current Behavior

The backend computes task throughput from the sum of fetched counts in metric
windows whose `window_ended_at` is inside the lookback. The task detail chart
then renders the returned windows as evenly spaced points.

Because the chart uses only existing windows, a paused task can still show a
full-looking activity line until the final pre-pause metric window leaves the
lookback. In a concrete local example, the task paused at `16:39:25` and a final
metric window ended at `16:39:33`; that window naturally remained visible until
approximately `16:54:33`.

## Proposed Behavior

### Throughput Card

When `task.viewStatus === "paused"`, display the effective current throughput as
`0/min`.

Supporting text should describe the status, for example "Paused" or "No new
fetches". The card should not imply an active stream while the task is paused.

For running, idle, failed, and offline tasks, keep using the plane-computed
`throughput_per_min` unless later design work changes those states too.

### Metric Chart

Render task metric windows on a time-based 15 minute axis ending at the current
client time.

- The right edge of the chart is always now.
- The left edge is now minus the configured lookback.
- Each metric window is plotted based on `window_ended_at`.
- Areas without windows are rendered as empty/zero activity, not compressed away.
- A paused task therefore shows any pre-pause windows toward the left/middle of
  the chart and a quiet region after the pause.

The chart title can remain "Task Metrics (Last 15m)" because the visual scope
will actually match that phrase. A compact paused-state hint may be shown when
the selected task is paused.

### Data Flow

Keep the existing task detail request:

`GET /api/v1/services/{service_name}/tasks/{task_name}?lookback_minutes=15`

The frontend already receives:

- `summary.view_status`
- `summary.throughput_per_min`
- `recent_metric_windows[]`
- `lookback_started_at`
- `lookback_minutes`

Implementation should pass the selected task status and lookback metadata into
the chart component. The chart can derive the current axis locally without
changing the API response shape.

## Edge Cases

- Paused immediately after activity: old windows remain visible in their real
  time positions, but the throughput card reads `0/min`.
- Paused for more than the lookback: chart shows no activity in the current
  lookback.
- Running task with sparse windows: chart still uses the current-time axis, so
  sparse activity does not appear denser than it really is.
- API or metric loading failure: keep the existing loading/error states.
- Browser clock skew: acceptable for this frontend-only design. If this becomes
  a production issue, the backend can expose a `server_now` timestamp later.

## Testing

- Add frontend tests for paused task display:
  - paused task throughput renders `0/min` even when historical
    `throughput_per_min` is nonzero.
  - chart maps windows onto a current-time axis and leaves post-pause quiet space.
- Keep existing API mapping tests for `throughput_per_min`.
- Manually verify the `cp-mysql-demo/dev` `produce_and_store` task:
  - pause the task;
  - confirm the status is paused;
  - confirm the throughput card shows current `0/min`;
  - confirm the chart does not visually compress pre-pause windows to the right
    edge.

## Scope Boundary

This change should stay in the frontend task detail presentation surface unless
testing proves the API needs a small additive field. Avoid touching reporter,
ingestion, database models, command dispatch, or control-plane protocol code.

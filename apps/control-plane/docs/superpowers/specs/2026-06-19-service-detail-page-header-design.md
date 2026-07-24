# Service Detail Page Header Design

Date: 2026-06-19
Status: Ready for user review
Owner: Codex

## Summary

Update only the OneStep Control Plane service detail page header to follow the
provided Page Header reference: a light gray preview band containing a compact
white header card with a back affordance, strong service title, primary actions,
and muted metadata lines.

The selected approach is a local service-detail header refactor. It preserves
all existing service detail data fetching, command dispatch behavior, lookback
filter behavior, status derivation, routes, and backend/API contracts.

## Goals

- Make the service detail header visually match the referenced Page Header
  component: gray stage, white low-radius card, back chevron, large title,
  orange primary action, and muted supporting lines.
- Keep the header utilitarian and scan-first in the Vibehub style.
- Keep all existing controls available: lookback segmented control, sync menu,
  restart action, and runtime signal metric.
- Limit the change to `ServiceDetailPage` and the service-detail header CSS.

## Non-Goals

- No redesign of task detail, instance detail, agent detail, or worker pages.
- No changes to command semantics, permission gates, reason dialogs, or API
  calls.
- No new shared component library unless the implementation needs a tiny local
  helper for readability.
- No changes to tables, side navigation, tabs, or lower service detail content.

## Design

The service detail topbar becomes:

1. A `service-detail-header-stage` with a warm light-gray background.
2. A centered `service-detail-header-card` with:
   - Left side: back chevron button/link, service name, status badge.
   - Right side: existing lookback control plus existing command actions.
   - Bottom metadata row: environment/last sync and primary runtime signal.
3. Responsive behavior:
   - Desktop: title/actions in one row, metadata beneath.
   - Mobile: title, actions, and metadata stack without overlap.

The orange accent is reserved for the primary command trigger and active focus
states. Restart remains visually dangerous via existing danger styling.

## Verification

- Run the focused service detail test if available:
  `pnpm test -- src/pages/service-detail/ServiceDetailPage.test.tsx`
- Run the frontend build:
  `pnpm build`
- Manually inspect `http://localhost:5173/services/<service>?environment=...`
  with realistic service data, checking desktop and mobile widths.

## Scope Boundary

Because the repository currently has unrelated uncommitted worker/backend/style
changes, implementation must avoid touching those files unless they are already
part of the service detail header surface.

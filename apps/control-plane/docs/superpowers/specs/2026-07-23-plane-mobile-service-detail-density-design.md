# Mobile Service Detail Density Design

**Date:** 2026-07-23

**Status:** Approved in visual review

## Goal

Make the service-detail view substantially denser below Tailwind's `sm` breakpoint without removing service controls or concealing service state. The current mobile layout gives the global connection bar, textual service-status badge, and three telemetry cards more vertical space than their information value warrants.

## Scope

This design applies to the service-detail route (`/services/{serviceId}`) when no task is selected.

- Compress the global API/environment control bar only on mobile. Keep its connection state, environment filter, refresh behavior, and 44px refresh target.
- Replace the service-level textual status badge with a status dot immediately after the service name in the breadcrumb on mobile. The dot must retain the existing `headerStatus` color mapping and expose its existing localized label through a tooltip and screen-reader text.
- Add numeric badges to the service `Tasks` and `Instances` tabs, sourced from `selectedService.totalTaskCount` and `selectedService.totalInstances` respectively. Display the badges at every breakpoint so the same service does not show conflicting counts by width.
- On mobile, hide the redundant total-instances telemetry card. Retain the health and throughput cards as a two-column grid, with reduced card height, padding, icon, and type scale. Desktop keeps the existing three-card telemetry grid.

## Non-Goals

- No API, reporter payload, routing, lifecycle, or command changes.
- No change to task-detail headers. They retain their textual status badge.
- No change to desktop service-header layout or desktop telemetry card dimensions.
- No new translation keys; reuse the existing localized status and tab labels.

## Responsive Layout

At widths below `sm`:

1. The global context bar retains its existing `px-3 py-2` rhythm and icon-only refresh control; avoid any fixed minimum height that expands it beyond its content.
2. The service breadcrumb is a single flexible row. Its service-name segment may ellipsize, but the status dot is `shrink-0` and remains immediately after that segment.
3. The service heading contains only the back control, service name, and description. The old bordered textual status badge is not rendered in this header.
4. The `Tasks` and `Instances` tabs show compact count badges. The active tab uses its current active color with a readable lighter count pill; inactive tabs use the existing neutral palette.
5. `TelemetryStats` hides the total-instances card. Health and throughput fill the existing two mobile columns, each at a compact fixed height that still accommodates the longest existing localized health copy without clipping.

At `sm` and above:

- Restore the current textual status badge beside the heading.
- Restore the existing three telemetry cards and their current dimensions.
- Keep task and instance tab count badges.

## Component Ownership

`frontend/src/App.tsx` owns the global context bar, service breadcrumb/header, and tab strip. It will:

- Branch the service-level mobile status presentation from the existing shared `headerStatus` markup, without altering task-detail presentation.
- Keep the visual dot `aria-hidden`, place the existing localized label in a screen-reader-only element, and attach the same label as `title` text for pointer users.
- Derive the two badge values directly in the existing `SERVICE_TABS.map` render from `selectedService`; do not duplicate counts in component state or fetch new data.

`frontend/src/components/TelemetryStats.tsx` owns telemetry-card visibility and density. It will use responsive utility classes on the existing cards rather than add a second mobile-only component. The total-instances card is hidden below `sm`; health and throughput compact below `sm` and retain their present structure at `sm` and above.

## Data And State Semantics

- `selectedService.totalTaskCount` and `selectedService.totalInstances` are already normalized by `mapService` and are the canonical service-summary counts. The count badges use these fields rather than filtered `activeServiceTasks` or `activeServiceInstances`, so their values match the telemetry and service-list summary.
- The status dot continues to use `getHeaderStatus(selectedService.viewStatus, selectedTask?.viewStatus, tr)`. In scope, no task is selected, so it represents the service status. Colors and localized wording do not change.
- The health and throughput cards remain the sole mobile telemetry cards. Moving the instance count into the Instances tab intentionally removes redundant information, not any operational data.

## Validation

Add focused coverage that verifies the service-detail tab strip renders the `Tasks` and `Instances` badges from the selected service summary and leaves `Logs` without a count. Update or add an end-to-end Playwright scenario with a mobile viewport that verifies:

1. The breadcrumb contains the service name followed by an accessible running-status dot, while the mobile heading has no visible `RUNNING`/`运行中` badge.
2. The `Tasks` and `Instances` tabs show the mocked service counts and still navigate normally.
3. The mobile telemetry region contains health and throughput but not the total-instances card, with no overlap or clipped text.
4. A desktop viewport retains the textual service-status badge and total-instances card, while tab counts remain visible.

Run the focused test files, then `pnpm build`. Visually inspect service-detail pages at a narrow mobile viewport and at desktop after rebuilding the plane image and restarting the `plane` container, as required for frontend image changes.

## Stop Conditions

Stop and re-evaluate if the total counts cannot be trusted as the canonical counts for the visible service, if the lowest supported mobile viewport cannot fit the translated tab labels and badges, or if the shared header cannot distinguish a service-detail route from a task-detail route without changing task behavior.

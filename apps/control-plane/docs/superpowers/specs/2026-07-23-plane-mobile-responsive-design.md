# Plane Mobile Responsive Design

Date: 2026-07-23
Status: Approved
Owner: Codex

## Summary

The onestep control plane will add a responsive mobile and tablet experience
for every authenticated product surface. Narrow viewports currently retain the
desktop side rail and its left content offset, which reduces usable width,
forces long identifiers into cramped columns, and stretches summary cards into
an unnecessarily long page.

The selected design replaces the side rail with bottom navigation below the
desktop breakpoint, compacts global status controls, uses purpose-built record
layouts for dense data, and reduces task summary metrics to a compact two-by-two
grid. Desktop information architecture and behavior remain unchanged.

This is a frontend-only change. It does not modify backend endpoints, reporter
payloads, WebSocket behavior, task lifecycle semantics, or remote-control
capabilities.

## Goals

- Make every authenticated page readable and navigable at 360px width, with
  every intentionally exposed mobile workflow fully operable.
- Recover the width currently consumed by the fixed mobile side rail.
- Preserve the existing desktop side rail, tables, and multi-column layouts.
- Keep high-frequency navigation available with one tap.
- Make operational data easy to scan without page-level horizontal scrolling.
- Reduce task-detail summary height so detailed content appears in the first
  viewport.
- Lower the visual weight of task commands while keeping them accessible.
- Preserve existing API, pending, error, toast, and routing behavior.
- Maintain complete keyboard, screen-reader, touch, and reduced-motion support.

## Non-Goals

- Do not redesign the desktop application.
- Do not introduce new commands, filters, API fields, or routes.
- Do not change control-plane protocol or reporter data.
- Do not build a separate mobile application or duplicate page-level data
  ownership.
- Do not add a new component framework or animation dependency.
- Do not turn every desktop table into a generic configurable card system.
- Do not expose instance restart or stop controls in mobile record cards.

## Assumptions

- The existing React 19, Tailwind CSS, Lucide, and interaction foundation remain
  in place.
- Viewports below 1024px include the mobile and tablet navigation experience.
- The current desktop layout becomes usable at 1024px and wider.
- The current login page is already structurally responsive; it remains in the
  verification matrix and only receives changes if verification finds a scoped
  overflow or touch-target defect.
- Mobile record layouts may reuse the same data mapping as desktop tables, but
  network requests and mutation ownership remain shared.
- The existing branch changes in `App.tsx`, `ServicesList.tsx`, interaction
  utilities, and their tests must be preserved.

## Approaches Considered

### Shared Responsive Shell With Targeted Page Variants - Selected

Keep the existing route and data ownership. Add a responsive navigation shell,
then adapt only the components that need a different narrow-screen structure.
This preserves behavior, limits duplication to presentation markup where a
table cannot become a usable phone layout through CSS alone, and isolates the
desktop experience from mobile changes.

### Global CSS Compression

Use media queries to shrink spacing, type, and side-rail width without changing
component structure. This would be smaller initially, but it cannot make dense
tables, multi-control toolbars, or modal geometry reliably usable. It would also
retain the fundamental width loss visible in the reported screenshot.

### Separate Mobile Pages

Create dedicated mobile page components and select them by viewport. This gives
maximum visual freedom but duplicates routing, async state, actions, and tests.
The maintenance and regression cost is not justified for a responsive version
of the same product workflows.

## Responsive Model

The implementation uses two structural modes and responsive grids within the
mobile mode:

- Below 1024px: hide the desktop side rail, remove the main-panel left offset,
  show bottom navigation, and reserve bottom safe-area space in the scrolling
  content.
- From 640px through 1023px: keep bottom navigation while allowing two-column
  record and metric layouts where their content fits.
- Below 640px: use phone spacing, compact controls, single-column detail
  sections, mobile record layouts, and bottom-aligned modal panels.
- At 1024px and wider: retain the existing desktop side rail, tables, dialogs,
  and multi-column layout.

Responsive behavior is CSS-driven. JavaScript must not read `window.innerWidth`
to choose route behavior or data ownership.

## Application Shell

### Desktop Navigation

The current fixed side rail remains visible at 1024px and wider. Its width,
labels, active state, external links, and logout behavior remain unchanged.

### Mobile Bottom Navigation

Below 1024px, render four stable destinations:

1. Overview
2. Services
3. Notifications
4. More

Overview, Services, and Notifications call the existing `onViewChange`
callback. Services remains active for service and task detail routes. Each item
has a Lucide icon, a localized label, a 44px or larger hit area, and
`aria-current="page"` when active.

The bar is fixed to the viewport and includes `env(safe-area-inset-bottom)`.
The main scrolling region receives matching bottom padding so the last content
item can scroll fully above the bar.

### More Panel

More opens a mobile panel containing locale, documentation, GitHub, and logout.
It reuses the existing locale and logout behavior and the existing external
URLs. The panel closes on selection, outside click, and `Escape`, and returns
focus to its trigger. It does not create a new route.

### Global Status Controls

The desktop command bar remains unchanged. Below 1024px it becomes a compact
single-row surface:

- API state remains visible through a status dot and concise label.
- Environment remains directly accessible.
- Refresh becomes an icon control with an accessible label and stable 44px hit
  area.
- Locale moves to More because it is not a high-frequency operational control.

The row wraps only as a fallback for unusually long translations; it must not
create page-level horizontal overflow.

## Page Designs

### Overview

- Use a compact two-by-two summary grid on phones and allow wider grids as space
  permits.
- Reduce card padding and height while preserving status context that changes
  the meaning of a value.
- Stack the service registry and recent activity sections.
- Render registry entries as full-width records with status, identity, task
  count, and instance count visible without horizontal scrolling.
- Long service names and identifiers truncate within their own line; they do
  not resize the containing layout.

### Services List

- Retain a single-column phone list and use two columns only when card contents
  remain readable.
- Keep each card as a navigation target with a full 44px or larger hit area.
- Allow names and descriptions to wrap; keep status and short operational values
  stable.
- Do not add mobile-only service commands to list cards.

### Service Detail

- Keep breadcrumb context to one truncating line above the title.
- Provide a 44px back control and allow the service or task title to wrap at any
  character, which is required for long generated task identifiers.
- Keep status visible without forcing the title onto an unusably narrow track.
- Render Tasks, Instances, and Logs as a full-width segmented row on phones.
- Use a compact two-column service summary grid where the existing value and
  label fit; otherwise let an individual card span the full row.
- Stack content sections and reduce gaps on phones.

### Task Detail

The approved mobile task header uses two low-emphasis icon controls:

- Manual run remains directly available as a Play icon.
- Restart and pause or resume move into an Ellipsis context menu.

Both controls have 44px hit areas, accessible labels, stable pending feedback,
and the same existing disabled prerequisites. The menu reuses existing command
handlers and pending state. It closes on selection, outside click, and `Escape`,
and returns focus to the Ellipsis trigger.

The four task metrics use a two-by-two phone grid. Each card is approximately
52px high and contains only its label and primary value. Secondary explanatory
copy and decorative progress are omitted on phones. The desktop cards keep their
current height and content.

Task details then stack in this order:

1. Topology
2. Resource chart
3. Configuration
4. Event diagnostics

Each section controls its own overflow. No child visualization, code block, or
identifier may widen the page.

### Tasks And Instances

Desktop presentation remains unchanged. Below 1024px, dense rows become record
cards rather than a horizontally scrolling table.

Mobile record cards expose all fields needed to identify and assess the record.
They do not show restart, stop, or other row-level operation buttons. Task
operations remain available from task detail. Instance cards are intentionally
read-only on mobile; desktop retains the existing instance commands.

On tablets, record cards may use a two-column grid. On phones they use one
column. Cards must not nest another card surface.

### Logs, Diagnostics, And Configuration

- Log metadata wraps into a readable header followed by a full-width message.
- Tracebacks and configuration content scroll inside their own code region when
  a line cannot safely wrap.
- Diagnostic filter controls wrap into multiple rows with 44px controls.
- Pagination uses full-size icon controls and remains inside its panel.
- The page itself never becomes a horizontal scroll container.

### Notification Settings

- Stack page heading, filters, channel records, and editor fields on phones.
- Channel summaries become readable records rather than compressed desktop
  columns.
- Management actions use a contextual menu when multiple commands would compete
  for width.
- Editor sections remain a single form and preserve existing validation, save,
  test, delete, and pending behavior.

### Dialogs And Panels

The manual-run dialog and notification editing surfaces use a bottom-aligned,
full-width panel below 640px. The panel respects the bottom safe area, caps its
height to the viewport, and gives its body independent vertical scrolling.

The header and final action area remain visible while long target lists or forms
scroll. Existing focus trapping, `Escape` handling, focus restoration, pending
state, and validation remain authoritative.

## Visual And Interaction Details

- Keep the existing neutral and indigo product palette.
- Reduce mobile spacing instead of scaling type with viewport width.
- Use existing card radii and layered low-opacity shadows consistently.
- Avoid adding decorative page-section cards or nested card surfaces.
- Use Lucide icons for navigation and familiar commands.
- Icon-only controls include accessible labels; desktop hover tooltips name
  unfamiliar controls.
- Every mobile interactive target is at least 44px by 44px and adjacent targets
  do not overlap.
- Mobile press feedback uses `scale(0.96)` with a transform-specific,
  interruptible transition. Desktop interaction behavior remains unchanged.
- Do not use `transition: all` or broad `will-change` declarations.
- Reduced-motion preferences remove nonessential transforms while preserving
  visible state changes.

## Data Flow

No server state changes are introduced. The responsive shell consumes the same
`currentView`, `onViewChange`, locale, and logout inputs as the existing side
rail. Page variants consume the same service, task, instance, log, notification,
and pending-state props as their desktop counterparts.

Task command changes are presentation-only. Manual run continues through the
existing dialog and handler. Restart and pause or resume continue through the
existing command handlers and `pendingTaskId` state. Refresh, deployment, and
notification mutations retain their current owners.

The mobile and desktop representations must not issue duplicate requests or
register duplicate global listeners. Inactive responsive markup may be hidden
with CSS only when it has no side effects; behaviorful overlays should be
mounted only when opened.

## Error And Async Behavior

- Loading skeletons must use mobile geometry at narrow widths and remain hidden
  from assistive technology as they are today.
- Background refresh keeps existing content visible.
- Pending controls preserve their dimensions and disable only conflicting
  actions.
- API errors, empty states, validation errors, and toasts keep their existing
  source of truth and semantics.
- Error text wraps within its panel and cannot force viewport overflow.
- Closing a menu or bottom panel never clears durable error state unless the
  existing owning component already does so.

## Accessibility

- Bottom navigation uses semantic navigation markup and exposes the current
  destination.
- More and task command menus retain keyboard dismissal and focus restoration.
- Bottom panels retain dialog semantics, focus containment, and labeled close
  controls.
- Touch targets are at least 44px in both dimensions.
- Long Chinese labels, English labels, generated identifiers, and numeric values
  fit without overlap or clipping.
- Color is not the only indicator of connection, status, pending, success, or
  failure.
- `prefers-reduced-motion` remains respected.

## Testing And Verification

Focused unit tests will cover:

- mobile navigation destinations, active state, and More panel behavior;
- More panel keyboard dismissal and focus restoration;
- task command menu command routing, disabled state, and dismissal;
- mobile instance record content and the absence of row command buttons;
- shared action handlers still receive the same identifiers;
- existing desktop component behavior remains intact.

Run the complete frontend unit suite and TypeScript/Vite build.

Playwright verification will cover at least 360x800, 390x844, 768x1024, and a
desktop viewport. It will visit overview, services, service detail, task detail,
instances, logs, notifications, manual run, and login. Checks include:

- `document.documentElement.scrollWidth` does not exceed viewport width;
- bottom navigation does not cover the final scrollable content;
- active navigation follows route changes and browser history;
- long Chinese text and generated identifiers do not overlap controls;
- menus and bottom panels remain within the viewport;
- every command intentionally exposed in the mobile UI is reachable and its
  pending feedback is visible;
- desktop side rail, tables, dialogs, and multi-column layouts do not regress;
- reduced-motion mode preserves usable state feedback.

Capture desktop and mobile screenshots for visual inspection. Check console
output and failed network requests during the exercised workflows.

Because frontend code is baked into the control-plane image, rebuild and restart
the service after implementation:

```bash
docker compose build plane
docker compose up -d plane
```

Confirm the `plane` container is healthy before final browser verification.

## Success Criteria

- All authenticated primary pages are readable and navigable at 360px width,
  and intentionally exposed mobile workflows remain operable.
- No tested page has page-level horizontal overflow.
- The desktop side rail does not consume width below 1024px.
- The mobile bottom bar does not obscure content or ignore safe-area insets.
- Task metrics occupy approximately half their current mobile height.
- Task commands match the approved low-emphasis Play plus Ellipsis treatment.
- Mobile record cards expose required data without row operation buttons.
- All mobile controls meet the 44px touch-target requirement.
- Desktop layout and behavior pass existing tests and visual verification.

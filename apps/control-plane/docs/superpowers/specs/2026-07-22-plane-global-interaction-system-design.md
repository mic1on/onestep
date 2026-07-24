# Plane Global Interaction System Design

Date: 2026-07-22
Status: Approved
Owner: Codex

## Summary

The onestep plane will adopt a global interaction system with a "Quiet
Precision" motion personality. The system prioritizes operational certainty:
after a user refreshes data, pauses or restarts a task, deploys an update, saves
settings, or runs a task manually, the interface must immediately show that the
action was received, keep its in-progress state local and legible, and report a
clear success or failure outcome.

This is a frontend-only change. It standardizes timing, easing, loading states,
page and overlay transitions, button feedback, menus, progress, and toast
notifications without changing the page information architecture, visual brand,
backend API, WebSocket protocol, or task lifecycle semantics.

## Goals

- Give every high-value operation an immediate, in-progress, and final state.
- Make first loads feel stable by preserving the final content layout with
  skeletons.
- Keep existing content visible during background refreshes so users do not lose
  context.
- Establish one restrained timing and easing system across the plane.
- Improve orientation during page, menu, and dialog transitions without adding
  decorative motion.
- Keep interactions accessible to keyboard, screen-reader, and reduced-motion
  users.
- Preserve current data ownership and avoid a new animation runtime dependency.

## Non-Goals

- Do not redesign page structure, navigation, typography, colors, or branding.
- Do not add new product features or operational commands.
- Do not modify API payloads, reporter fields, WebSocket behavior, remote task
  control, or task event semantics.
- Do not introduce Framer Motion or another animation library.
- Do not add looping animation to cards, navigation, or static status content.
- Do not replace the existing topology flow visualization.

## Assumptions

- The plane remains a React 19 and Tailwind CSS application.
- Existing async booleans and task identifiers remain the source of truth for
  pending state.
- Existing user changes in `App.tsx`, `ServicesList.tsx`, and their tests are
  preserved and incorporated rather than reverted.
- Continuous topology motion remains meaningful because it communicates active
  data flow. Other continuous motion is unnecessary.
- The current four-second toast lifetime remains appropriate.

## Chosen Approach

### Lightweight Interaction Foundation

Use CSS custom properties and a small set of semantic interaction classes. Keep
async state in the React components that already own each operation. Add a small
shared component only when it contains behavior that would otherwise stay
tangled in `App.tsx`, such as the toast viewport.

The foundation lives in `frontend/src/index.css` and defines:

- `120ms` for hover, press, and other micro-feedback;
- `220ms` for menus, local state transitions, and page entry;
- `320ms` for dialogs and larger overlays;
- an ease-out curve for entering and responding;
- an ease-in curve for exiting;
- semantic classes for pressable controls, content entry, popovers, skeletons,
  and reduced-motion behavior.

The implementation should favor `transform` and `opacity`. Progress bars should
use `scaleX()` with a left transform origin instead of animating width. Motion
must remain interruptible and must never block user input after the underlying
state is ready.

## Interaction State Model

Each asynchronous mutation follows the same visible state sequence:

1. `idle`: the action is available when its existing prerequisites are met.
2. `pending`: the initiating control keeps its dimensions, shows a spinner and
   pending label, exposes `aria-busy`, and disables only conflicting actions.
3. `success`: refreshed runtime data becomes the primary confirmation; a success
   toast summarizes the outcome.
4. `error`: affected controls become usable again, related inline error content
   remains visible where available, and a warning toast reports the failure.

Pending state remains local. Pausing one task must not disable controls for other
tasks. A deployment may disable deployment controls for the selected service but
must not freeze unrelated navigation or reading.

## Interaction Behavior

### Initial Loading

When no API-backed service content exists yet, render skeletons that match the
eventual header, summary, and list geometry. This prevents a centered spinner or
loading label from being replaced by a differently sized page. The connection
status remains visible and announces connecting, connected, or fallback state.

Skeleton shimmer is subtle and stops under reduced motion. Skeleton blocks are
hidden from assistive technology; a concise live loading label provides the
semantic state.

### Background Refresh

When content is already present, keep it rendered during refresh. Only the
refresh control enters pending state. On success, the refreshed region receives
a brief low-contrast highlight and the app shows a success toast. On failure,
the existing data remains visible and the existing demo/fallback semantics are
preserved.

### Page Navigation

New page content enters over `220ms` with opacity and at most `6px` of vertical
movement. There is no wait-for-exit choreography; navigation and browser history
remain immediate. Page animation must not reset scroll or delay focus.

### Buttons And Links

Interactive controls use a `120ms` color, border, and shadow response. Pressed
controls move down by at most `1px`; they do not bounce or visibly scale. Focus
rings remain immediately visible and are not animated away.

Loading icons replace existing icons in place. Controls with changing labels
use stable minimum dimensions so `Refresh` to `Refreshing` and similar changes
do not shift adjacent content.

### Menus

Environment, locale, and task action menus enter over `220ms` using opacity and
at most `4px` of movement from their trigger. They close on selection, outside
click, or `Escape`. Opening moves focus into the menu when appropriate; closing
returns focus to the trigger. Visual exit motion must not delay closure.

### Dialogs

The manual-run dialog uses a fading backdrop and an opacity plus
`scale(0.985)` panel entry over at most `320ms`. Opening focuses the first useful
field or control, `Escape` closes the dialog, focus remains trapped while open,
and closing returns focus to the launch control.

### Toasts

The toast viewport is an `aria-live` region at the existing lower-right
location. Toasts enter with opacity and `6px` of movement and retain success,
information, and warning semantics. Each toast includes a close button with an
accessible label. Automatic dismissal remains four seconds, and manual dismissal
is immediate.

Warning/error messages use alert semantics when user attention is required;
success and information messages use polite status semantics.

### Progress And Runtime Status

Deployment progress updates its percentage and transforms a stable progress fill.
Restart and task-command feedback remains next to the initiating action rather
than replacing the page. Runtime status dots do not pulse merely to decorate;
continuous movement is reserved for active processing where it communicates
meaning.

The existing `TopologyFlow` staged animation remains the primary continuous
motion surface. It reuses the global motion variables where practical and keeps
its current reduced-motion behavior.

## Component Scope

### `frontend/src/index.css`

- Add the motion tokens, semantic interaction classes, skeleton keyframes, and
  global reduced-motion override.
- Keep existing font, logo, and scrollbar styling unchanged.

### `frontend/src/App.tsx`

- Distinguish initial loading from background refresh.
- Apply page entry behavior to the active content stage.
- Standardize refresh, deploy, restart, manual-run, and task-command pending
  feedback.
- Improve dialog focus and motion behavior.
- Render the accessible toast viewport.

### Shared Or Local Components

- Extract a small `ToastViewport` only if it meaningfully isolates timeout,
  dismissal, semantic role, and rendering behavior from `App.tsx`.
- Keep a one-off control-plane initial skeleton local rather than building a
  speculative skeleton component library.
- Avoid a universal animated button wrapper; existing buttons keep their visual
  variants and adopt the shared semantic classes.

### Existing Feature Components

- `Sidebar`: consistent press, focus, and active navigation feedback.
- `ServicesList`: stable table/list entry and row feedback without card lift.
- `TasksList`: local pending state, stable controls, and accessible menu behavior.
- `NotificationSettingsPage`: consistent load, save, test, and delete feedback.
- `ResourceChart` and `TaskEventDiagnostics`: consistent non-blocking refresh,
  loading, empty, and error transitions.
- `TopologyFlow`: preserve staged flow motion and align timing variables where
  this does not change its meaning.

## Data Flow And Error Handling

No new server state or API contract is introduced. Existing request handlers
continue to own network calls and update the current React state. Interaction
styles consume existing state such as `isLoadingApi`, `isDeploying`,
`isRestartingAll`, `pendingTaskId`, and component-local loading flags.

Action failures always restore the corresponding control to a usable state in a
`finally` path or the existing equivalent. Request errors remain available in
their related page or panel when the current design already renders them. Toasts
supplement this local state and never become the only durable explanation for a
failed operation.

## Accessibility

- Respect `prefers-reduced-motion: reduce` for all new and existing continuous
  motion covered by the interaction foundation.
- Preserve visible `:focus-visible` rings.
- Use `aria-busy` and live regions for async state without announcing every
  decorative visual change.
- Keep menu and dialog keyboard behavior complete.
- Ensure skeletons and spinners do not introduce duplicate spoken labels.
- Keep buttons, progress, toasts, and long translated labels within stable,
  responsive bounds on desktop and narrow screens.

## Testing And Verification

Focused component and app tests will verify:

- initial skeleton rendering versus content-preserving background refresh;
- task pending state disables only the affected task actions;
- toast roles, accessible labels, manual dismissal, and automatic dismissal;
- dialog focus entry, `Escape` closure, and focus restoration;
- menu `Escape` behavior where the touched component exposes a menu;
- existing action success and error paths remain intact.

Run the complete frontend unit suite and TypeScript/Vite build. Use Playwright at
desktop and narrow mobile viewports to inspect pages, menus, the dialog, loading
states, progress, toasts, and long labels for clipping or overlap. Emulate
`prefers-reduced-motion` and verify that continuous animation stops while visible
state feedback remains.

Because frontend code is baked into the control-plane image, rebuild and restart
the `plane` service after implementation:

```bash
docker compose build plane
docker compose up -d plane
```

Confirm the container is healthy before browser verification.

## Alternatives Considered

### Framer Motion

This would provide richer presence and layout animation primitives, but it adds a
runtime dependency and encourages broader component conversion. The plane does
not need that complexity for restrained operational feedback.

### Ad Hoc CSS In Each Component

This would minimize the first diff, but timing, easing, loading feedback, and
reduced-motion behavior would continue to diverge. A small shared CSS foundation
offers better consistency without a component framework.

## Control-Plane Coordination Boundary

This design changes only frontend presentation and client-side interaction
feedback. It does not change reporter payload fields, topology data, lifecycle
events, WebSocket protocol behavior, runtime identity, or remote task control.
No cross-repository control-plane protocol coordination is required.

# Shared Task Lookback Toggle Design

**Date:** 2026-07-24

**Status:** Approved in conversation

## Goal

Make both task-detail lookback controls consistent by placing `Custom` directly after the existing `30m` preset and showing the custom-minute form only after the user selects it.

## Scope

This change applies to the lookback controls in `ResourceChart` and `TaskEventDiagnostics` through one shared component.

- Add `Custom` as the final option in the existing preset button group.
- Keep the custom minutes input, unit label, and Apply button hidden while a preset is selected.
- Show that form immediately to the right of the button group when `Custom` is selected.
- Hide the form when the user selects `5m`, `10m`, `15m`, or `30m`.
- Preserve the last custom input value when the form is hidden and shown again.
- Keep the existing 1-to-1440-minute normalization and callback behavior.

## Non-Goals

- No API, backend, reporter payload, chart rendering, event rendering, or pagination changes.
- No new lookback presets or changes to the supported custom range.
- No changes to existing translations.
- No persistence across page reloads.

## Interaction And State

`LookbackControl` will own a local boolean that records whether the custom editor is open, alongside the custom input value. `ResourceChart` and `TaskEventDiagnostics` will pass their accessible label, presets, maximum value, applied value, and change callback into this shared component.

1. The initial view selects the matching preset and hides the custom editor.
2. Clicking `Custom` opens the editor without changing the applied lookback or issuing a request. The button receives the active visual treatment and `aria-pressed=true`.
3. Editing the input changes only the local custom draft.
4. Submitting the form normalizes the draft, calls `onLookbackMinutesChange` when the value differs from the applied lookback, and leaves the custom editor open.
5. Clicking a preset closes the editor, applies that preset, and leaves the custom draft unchanged.
6. Clicking `Custom` again restores the previous draft.

The custom editor's open state, rather than whether the applied value happens to match a preset, controls the selected state of `Custom`. This preserves a clear response to the user's most recent mode choice. While the editor is open, preset buttons are not shown as pressed.

## Component Ownership

`frontend/src/components/LookbackControl.tsx` is the sole owner of preset selection, custom-mode visibility, draft retention, normalization, and the shared markup. It accepts only the values needed by both consumers and does not know whether it is controlling metrics or task events.

`ResourceChart` and `TaskEventDiagnostics` retain ownership of data fetching and applied lookback state. They render `LookbackControl` with their existing constants and callbacks and do not duplicate any time-range button or custom-form logic.

## Layout And Accessibility

The `Custom` button uses the same height, padding, selected colors, and hover treatment as the four preset buttons. Each consumer supplies its existing group label, so assistive technology continues to distinguish the task-metrics selector from the task-events selector.

The conditional form remains adjacent to the group in the existing wrapping flex row. On narrow widths it may wrap as one complete form; its input label, numeric bounds, unit text, and submit semantics remain unchanged.

## Validation

Add focused shared-component tests that verify:

- The custom input and Apply button are absent initially.
- Clicking `Custom` shows the form and marks `Custom` pressed.
- Applying a custom value emits that value and keeps the form visible.
- Selecting a preset hides the form and emits the preset.
- Reopening `Custom` restores the previous draft value.

Keep focused consumer tests that prove both `ResourceChart` and `TaskEventDiagnostics` wire the shared control to their callbacks. Update the existing Playwright task-detail scenario to confirm that both collapsed controls contain `Custom` after `30m`, open their own form on click, and close it after selecting a preset. Then run the focused frontend tests and production build.

Because this is frontend code baked into the control-plane image, rebuild and restart the `plane` container after implementation, then verify that it becomes healthy and visually inspect the task-detail view at desktop and mobile widths.

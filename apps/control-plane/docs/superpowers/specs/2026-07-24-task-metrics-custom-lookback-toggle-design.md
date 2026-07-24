# Task Metrics Custom Lookback Toggle Design

**Date:** 2026-07-24

**Status:** Approved in conversation

## Goal

Make the task-metrics lookback control more compact by placing `Custom` directly after the existing `30m` preset and showing the custom-minute form only after the user selects it.

## Scope

This change applies only to the lookback control in `frontend/src/components/ResourceChart.tsx`.

- Add `Custom` as the final option in the existing preset button group.
- Keep the custom minutes input, unit label, and Apply button hidden while a preset is selected.
- Show that form immediately to the right of the button group when `Custom` is selected.
- Hide the form when the user selects `5m`, `10m`, `15m`, or `30m`.
- Preserve the last custom input value when the form is hidden and shown again.
- Keep the existing 1-to-1440-minute normalization and callback behavior.

## Non-Goals

- No API, backend, reporter payload, chart rendering, or task-event diagnostics changes.
- No new lookback presets or changes to the supported custom range.
- No changes to existing translations.
- No persistence across page reloads.

## Interaction And State

`ResourceChart` will own a local boolean that records whether the custom editor is open, alongside the existing custom input value.

1. The initial view selects the matching preset and hides the custom editor.
2. Clicking `Custom` opens the editor without changing the applied lookback or issuing a request. The button receives the active visual treatment and `aria-pressed=true`.
3. Editing the input changes only the local custom draft.
4. Submitting the form normalizes the draft, calls `onLookbackMinutesChange` when the value differs from the applied lookback, and leaves the custom editor open.
5. Clicking a preset closes the editor, applies that preset, and leaves the custom draft unchanged.
6. Clicking `Custom` again restores the previous draft.

The custom editor's open state, rather than whether the applied value happens to match a preset, controls the selected state of `Custom`. This preserves a clear response to the user's most recent mode choice. While the editor is open, preset buttons are not shown as pressed.

## Layout And Accessibility

The `Custom` button uses the same height, padding, selected colors, and hover treatment as the four preset buttons. The existing group label remains unchanged, so assistive technology continues to identify the whole control as the task-metrics lookback selector.

The conditional form remains adjacent to the group in the existing wrapping flex row. On narrow widths it may wrap as one complete form; its input label, numeric bounds, unit text, and submit semantics remain unchanged.

## Validation

Add focused component tests that verify:

- The custom input and Apply button are absent initially.
- Clicking `Custom` shows the form and marks `Custom` pressed.
- Applying a custom value emits that value and keeps the form visible.
- Selecting a preset hides the form and emits the preset.
- Reopening `Custom` restores the previous draft value.

Update the existing Playwright task-detail scenario to confirm that the collapsed control contains `Custom` after `30m`, opens the form on click, and closes it after selecting a preset. Then run the focused frontend tests and production build.

Because this is frontend code baked into the control-plane image, rebuild and restart the `plane` container after implementation, then verify that it becomes healthy and visually inspect the task-detail view at desktop and mobile widths.

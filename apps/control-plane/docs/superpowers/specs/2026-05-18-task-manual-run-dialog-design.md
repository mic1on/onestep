# Task Manual Run Dialog Design

Date: 2026-05-18
Status: Ready for user review
Owner: Codex

## Summary

Refine the task "manual run" dialog so it feels consistent with the current Signal Console surfaces and no longer expands into an oversized, legacy-styled modal.

Scope is limited to the manual run dialog opened from task command controls.

Primary files expected in scope:

- `frontend/src/features/tasks/components/TaskCommandControls.tsx`
- `frontend/src/styles/nothing-signal-console.css`
- tests covering the dialog layout and behavior

This change is presentation-only. It does not alter command payload shape, dispatch semantics, validation rules, or API contracts.

## Design Inputs

The following decisions were validated in conversation:

- Keep the interaction as a centered modal dialog
- Do not convert it into a drawer
- Make the dialog noticeably more compact than the current `60rem` layout
- Align the visual language with the current Signal Console / Nothing-inspired frontend
- Keep the flow target-first
- Give the JSON payload editor real vertical space
- Use the `A2 / Tall JSON` direction rather than a tiny single-line or near-single-line payload area

## Goals

1. Reduce the perceived size and visual heaviness of the manual run dialog.
2. Make the dialog feel visually consistent with the current services and detail surfaces.
3. Preserve manual run usability for multi-line JSON payloads.
4. Keep the targeting flow readable and safe without turning the dialog back into a large control surface.

## Non-Goals

- No backend or API changes
- No change to manual run validation rules
- No change to the reason requirement
- No change to how targets are selected
- No switch to a side drawer or full-screen experience
- No redesign of the destructive review dialogs in this iteration

## Current Problems

The current dialog combines two issues:

1. It inherits legacy / Apple-workbench dialog styling and therefore feels out of place next to the newer Signal Console surfaces.
2. It uses a very wide modal shell, but the internal layout still leaves the JSON payload feeling cramped relative to its actual use case.

This creates the worst combination:

- the dialog feels large
- the part that needs space, the JSON editor, still feels too small

## Approach Options

### Recommended: Compact target-first modal with tall JSON editor

Keep the modal centered and target-first, but rebalance the interior:

- smaller overall shell width than today
- shorter and cleaner header
- tighter instance list presentation
- taller payload editor
- shorter reason field

Why this approach:

- preserves the existing mental model
- solves the user complaint directly
- keeps the safest first-read emphasis on target selection
- gives JSON enough room without expanding back to a giant modal

### Alternative: Payload-first compact modal

Move the JSON editor above instance selection.

Why not now:

- better for payload-heavy edits, but weaker for safe target confirmation
- conversation settled on target-first

### Alternative: Split-pane modal

Put targets and payload side-by-side inside a wider dialog.

Why not now:

- increases tooling density
- pushes the dialog back toward a large, heavy control surface
- conflicts with the goal of reducing perceived size

## Layout Design

### Shell

The dialog remains a centered modal using the existing `OverflowDialog` interaction model.

Changes:

- reduce the manual run dialog width from the current `60rem` to a medium-width shell
- keep enough width for readable JSON, but avoid a full utility-panel feel
- preserve overflow safety for small screens

### Header

The modal header should be shorter and cleaner than today.

Rules:

- keep a single title in the dialog chrome
- keep the short subtitle in the dialog chrome
- remove the duplicated title/subtitle block from the body
- preserve the close button

### Body Order

Body order stays target-first:

1. target selection section
2. payload section
3. reason section
4. inline error feedback
5. actions

### Target Selection Section

The target section remains the first major block, but should read lighter and denser.

Rules:

- keep select-all and clear-selection actions
- keep the current checkbox-based selection behavior
- keep per-instance state metadata
- reduce padding and visual bulk of each row
- align row styling with Signal Console borders, type, and contrast

### Payload Section

The payload editor should follow the approved `A2 / Tall JSON` direction.

Rules:

- keep payload below targets
- make the payload textarea tall enough for real multi-line JSON editing
- use monospace typography
- visually prioritize the payload more than the reason field
- avoid making the payload so tall that the dialog becomes a full-screen editor

Practical interpretation:

- noticeably taller than the current 10-row field feels in practice
- tall enough that a small nested JSON object is readable without immediately feeling cramped

### Reason Section

The reason input remains required, but must read as secondary to payload editing.

Rules:

- keep it below payload
- reduce its visual prominence
- preserve multi-line input support

## Visual System

The manual run dialog should move away from the current Apple-workbench dialog treatment and align with the Signal Console language already used elsewhere.

Use:

- Signal Console borders and surface colors
- Space Grotesk / Space Mono typography patterns already used by the current frontend
- restrained, technical controls rather than soft or glossy cards
- compact spacing with clear section separation

Avoid:

- legacy warm translucent modal styling
- oversized internal cards
- decorative softness that conflicts with the operator-console look

## Implementation Boundaries

Prefer surgical changes:

- keep `OverflowDialog` as the interaction container unless a tiny shared hook or class adjustment is strictly needed
- keep `TaskCommandControls.tsx` as the behavioral owner of the dialog
- move the manual-run-specific visual treatment into the Signal Console stylesheet layer rather than extending the Apple-workbench styling further

Acceptable JSX changes:

- remove duplicated body heading copy
- regroup sections for cleaner spacing
- add only the small wrapper elements necessary to separate target, payload, and reason blocks

Out of bounds:

- changing the command dispatch flow
- changing the mutation contract
- introducing a new generic dialog framework

## Testing

Add or update tests to cover:

1. the manual run dialog still opens from the existing action
2. the dialog renders the target selection section before payload and reason
3. the dialog keeps the payload textarea and reason textarea present
4. any removed duplicate heading copy is intentionally absent if tested at the component level

Testing can stay at the existing frontend component-test level. No browser E2E is required for this iteration.

## Success Criteria

1. The manual run dialog appears visibly smaller and more compact than the current implementation.
2. The dialog uses the current Signal Console visual language rather than the legacy Apple-workbench style.
3. The JSON payload area is large enough for normal multi-line JSON editing without feeling cramped.
4. The flow remains target-first.
5. Existing manual run validation and submission behavior remain unchanged.

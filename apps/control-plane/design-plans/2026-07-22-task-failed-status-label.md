# Preserve Failed Task Status On Task Detail

Written against: ce71182a18ee9ef9e89fc01dbce0d65466f4d526

## Evidence chain

- Surface: `/services/{serviceId}/tasks/{taskId}` when the selected task has `viewStatus: "failed"`.
- Problem: the task list labels this state `Failed`, but the task-detail header labels the same state `DEGRADED`.
- Design evidence: `frontend/src/types.ts` defines task `failed` separately from service `degraded`; `frontend/src/components/TasksList.tsx` maps failed tasks to `status.failed`; `frontend/src/App.tsx` says the selected task status takes precedence but combines `failed` and `degraded` under `common.degraded` in `getHeaderStatus`.
- Owner: `frontend/src/App.tsx`, specifically `getHeaderStatus` and its task-detail header consumer.
- Scope and affected surfaces: the status badge on task-detail routes; service-detail status badges continue to use service status.
- Uncertainty: none.

## Design decision

Keep task `failed` and service `degraded` as distinct labels in `getHeaderStatus`. A failed task must use the existing `status.failed` translation while retaining the current amber badge and dot styling. A degraded service must continue to use `common.degraded` with the same styling.

## Reuse

- Existing i18n key: `status.failed` in `frontend/src/i18n.tsx` for both English and Chinese.
- Existing presentation: `bg-amber-50 text-amber-700 border-amber-200` and `bg-amber-500` from the current failed/degraded header branch.
- Exemplar: failed task labels and amber text in `frontend/src/components/TasksList.tsx`.

## Changes

1. `frontend/src/App.test.ts`
   - Change: add `status.failed: "Failed"` to the test translator and add a `getHeaderStatus` regression case asserting that a selected failed task is labelled `Failed`, even when its service is running.
   - Preserve: the existing idle-task and stopped-service assertions.
   - Verify: the new test fails against the written-against commit because the formatter returns `DEGRADED`.
2. `frontend/src/App.tsx`
   - Change: split the combined `failed || degraded` branch in `getHeaderStatus`; return `t("status.failed")` for `failed`, and keep `t("common.degraded")` for `degraded`.
   - Preserve: the current amber class names, dot color, task-status precedence, and mappings for running, paused, idle, and offline/stopped states.
   - Verify: a failed task detail badge reads `Failed` in English and `失败` in Chinese; a degraded service detail badge still reads `DEGRADED` or `降级`.

## Scope

- Inherit: every task-detail route whose selected task reports `viewStatus: "failed"`.
- Verify: service-detail routes with `viewStatus: "degraded"`, plus idle, paused, running, and offline task details.
- Exclude: backend status derivation, task list styling, command availability, i18n wording changes, and status color redesign.

## Validation

- Product: open a failed task from a service task list; the list and detail header must both identify it as failed.
- Interface: verify English and Chinese labels on a failed task detail, then return to a degraded service detail and confirm its label remains degraded.
- System: confirm `status.failed` remains the single translation owner and no parallel failed-task label or new badge variant is introduced.
- Repository: `cd frontend && pnpm exec vitest run src/App.test.ts` -> all focused unit tests pass.
- Repository: `cd frontend && pnpm build` -> TypeScript checks and the Vite production build pass.

## Stop conditions

- Stop if a current backend or product contract explicitly requires task `failed` to be presented as service-level `degraded`; resolve that semantic conflict before changing the label.

## Design documentation

- After acceptance and validation: none; this restores the existing task-status vocabulary rather than adding a new design rule.

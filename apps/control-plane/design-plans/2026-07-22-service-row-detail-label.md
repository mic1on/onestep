# Label Service Rows With Their Actual Destination

Written against: ce71182a18ee9ef9e89fc01dbce0d65466f4d526

## Evidence chain

- Surface: `/services`, in the Actions column of each service row.
- Problem: every service row says `View Task Details` / `查看任务详情`, although activating the row opens that service's detail page.
- Design evidence: `frontend/src/components/ServicesList.tsx` receives `Service[]` and calls `onSelectService(svc.id)` from the row; `frontend/src/App.tsx` wires that callback to `navigateToService`; `frontend/src/appRoute.ts` resolves the result as `/services/{serviceId}`; `frontend/src/i18n.tsx` already owns the matching `service.detail` label.
- Owner: `frontend/src/components/ServicesList.tsx`.
- Scope and affected surfaces: the destination label in service-list rows; task-card actions remain task-specific.
- Uncertainty: none.

## Design decision

Use the existing `service.detail` translation for the service-row destination label. This makes the visible label agree with the unchanged service-detail navigation without introducing another translation key or changing the row interaction.

## Reuse

- Existing i18n key: `service.detail` (`Service Detail` / `服务详情`) in `frontend/src/i18n.tsx`.
- Existing navigation owner: `onSelectService` in `frontend/src/components/ServicesList.tsx`, wired to `navigateToService` in `frontend/src/App.tsx`.
- Exemplar: `button.viewTaskDetails` remains correctly used by the actual task action in `frontend/src/components/TasksList.tsx`.

## Changes

1. `frontend/src/components/ServicesList.test.tsx`
   - Change: allow the test render helper to accept an `onSelectService` spy, then add a regression case asserting that a service row displays `Service Detail`, does not display `View Task Details`, and still calls `onSelectService` with the row's service id when its destination label is activated.
   - Preserve: existing title, status-filter, identity, description, and search tests.
   - Verify: the label assertion fails against the written-against commit while the callback assertion documents the behavior that must not change.
2. `frontend/src/components/ServicesList.tsx`
   - Change: replace `t("button.viewTaskDetails")` in the service-row Actions cell with `t("service.detail")`.
   - Preserve: row-level click handling, ChevronRight icon, hover animation, table layout, filters, and service data rendering.
   - Verify: English rows show `Service Detail`, Chinese rows show `服务详情`, and either row content or the destination label still opens the selected service.

## Scope

- Inherit: every row rendered by `ServicesList` on `/services`.
- Verify: task cards under `/services/{serviceId}` still show `View Task Details` / `查看任务详情` and still navigate to task detail.
- Exclude: route definitions, navigation state, service or task data models, table layout, and any broader copy rewrite.

## Validation

- Product: activate the destination label for a service row; the label identifies a service detail destination and the URL becomes `/services/{serviceId}`.
- Interface: verify the Actions column in English and Chinese, including a service with a long display name; the label and Chevron remain contained in the existing cell.
- System: confirm `service.detail` is reused and `button.viewTaskDetails` remains owned only by task-detail entry points.
- Repository: `cd frontend && pnpm exec vitest run src/components/ServicesList.test.tsx` -> all focused component tests pass.
- Repository: `cd frontend && pnpm build` -> TypeScript checks and the Vite production build pass.

## Stop conditions

- Stop if the service-row interaction has changed to open a task route or a task picker; in that case, re-audit the destination before selecting its label.

## Design documentation

- After acceptance and validation: none; this corrects a destination label by reusing an existing translation owner.

# Preserve Service Identity Through Detail Navigation

Written against: ce71182a18ee9ef9e89fc01dbce0d65466f4d526

## Evidence chain

- Surface: `/services/{serviceId}` and the service breadcrumb on `/services/{serviceId}/tasks/{taskId}`.
- Problem: the services list shows the canonical service display name, but the detail breadcrumb keeps only the text before the first space and the service-detail H2 replaces the name with the generic `Service Detail` label.
- Design evidence: `frontend/src/api.ts` owns the canonical display value as `${service.name} / ${service.environment}` and assigns it to `Service.name`; `frontend/src/components/ServicesList.tsx` renders that value unchanged; `frontend/src/App.tsx` changes it with `split(" ")[0]` in the breadcrumb and substitutes `service.detail` in the service-detail H2, while the sibling task-detail H2 uses the selected task's actual name.
- Owner: `frontend/src/App.tsx`, specifically the shared service/task detail heading composition.
- Scope and affected surfaces: service-detail headings and the service crumb on both service and task detail routes.
- Uncertainty: none.

## Design decision

Treat `selectedService.name` as the canonical service identity everywhere in the detail heading composition. Render it unchanged in the service breadcrumb and in the H2 whenever no task is selected. Keep the selected task name as the H2 on task-detail routes.

## Reuse

- Existing owner: `displayServiceName` and `mapService` in `frontend/src/api.ts`.
- Existing value: `selectedService.name` in `frontend/src/App.tsx`; do not reconstruct a name from `apiName`, `environment`, or route parameters.
- Exemplars: full `svc.name` rendering in `frontend/src/components/ServicesList.tsx`, and full `selectedTask.name` rendering in the existing task-detail H2.

## Changes

1. `frontend/e2e/app.spec.ts`
   - Change: update the existing service-detail assertions to require a heading named `billing-sync / prod` instead of the generic `Service Detail`; update the task-detail breadcrumb assertion to activate the service crumb named `billing-sync / prod` instead of the shortened `billing-sync`.
   - Preserve: the mocked API payloads, service-selection flow, task navigation, URLs, and all command assertions.
   - Verify: the updated heading and breadcrumb assertions fail against the written-against commit.
2. `frontend/src/App.tsx`
   - Change: replace both `selectedService.name.split(" ")[0]` breadcrumb values with `selectedService.name`; replace the no-task H2 value `tr("service.detail")` with `selectedService.name`.
   - Preserve: `selectedTask.name` as the task-detail H2, breadcrumb navigation handlers, service/task descriptions, status badges, action buttons, tabs, and route state.
   - Verify: the service list, service detail heading, and task-detail service crumb all show the same `name / environment` value supplied by `mapService`.

## Scope

- Inherit: all service-detail tabs and all task-detail routes composed by `App`.
- Verify: API-backed service names containing spaces, separators, and environment suffixes; the existing demo fallback names; task names remain unchanged.
- Exclude: changing `displayServiceName`, separating environment into a new badge, route encoding, service-list content, typography, header layout redesign, and task identity.

## Validation

- Product: select `billing-sync / prod` from `/services`, then open a task; the service detail H2 and task-detail service crumb must preserve that exact service identity.
- Interface: verify the service detail and task detail at desktop and narrow widths with a multiword service name; preserve the full DOM string, use the layout's existing wrapping or overflow-containment utilities, and do not allow the name to overlap adjacent status or action controls.
- System: confirm `selectedService.name` remains the only consumed display value and no parallel service-name formatter is added to `App`.
- Repository: `cd frontend && pnpm exec playwright test e2e/app.spec.ts --grep "renders the control plane dashboard|loads API-backed service"` -> the focused service-navigation scenarios pass.
- Repository: `cd frontend && pnpm build` -> TypeScript checks and the Vite production build pass.

## Stop conditions

- Stop if `Service.name` no longer represents the intended display identity or if a current accepted design explicitly separates service name and environment into different owners; update this plan against that owner before implementation.

## Design documentation

- After acceptance and validation: none; this restores consistent use of the existing service display-name owner.

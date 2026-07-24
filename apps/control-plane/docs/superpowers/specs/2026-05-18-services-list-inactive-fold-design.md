# Services List Inactive Fold Design

Date: 2026-05-18
Status: Ready for user review
Owner: Codex

## Summary

Move long-inactive services to the bottom of the services list and render them inside a collapsed section by default.

Scope is limited to:

- `frontend/src/pages/services-list/ServicesListPage.tsx`
- `frontend/src/pages/services-list/ServicesListPage.test.tsx`
- `frontend/src/lib/runtime-config.ts`
- `frontend/.env.example`

This is a frontend-only change. It does not change APIs, backend behavior, routes, or service data contracts.

## Design Inputs

The following decisions were validated in conversation:

- Inactive services should not be hidden completely
- Inactive services should appear at the end of the list
- The inactive group should be collapsed by default
- The inactivity threshold should be configurable by env
- Default inactivity threshold should be `3` days
- Activity should be determined from `last_seen_at`

## Goals

1. Keep the default service list focused on active services.
2. Preserve visibility of inactive services without mixing them into the main scan path.
3. Keep implementation small and isolated to the existing frontend list surface.

## Non-Goals

- No backend-side inactive classification
- No deletion, archival, or hiding logic
- No new user-facing filters or toggles beyond the collapsed section
- No change to the existing summary chip semantics in this iteration
- No change to service detail behavior

## Approach Options

### Recommended: Frontend-only grouping

After existing environment, source-kind, and search filtering, split the visible services into two groups:

- active services
- inactive services

Render active services in the current table body and inactive services in a separate `<details>` section below the table.

Why this approach:

- smallest change surface
- no API or backend dependency
- easy to tune with env
- preserves existing row rendering and ordering logic

### Alternative: Backend-provided inactive flag

The API could return an `inactive` boolean or separate arrays.

Why not now:

- expands the change across backend and shared API types
- unnecessary for a presentation-only behavior
- adds coordination cost for a simple rule

## Inactivity Rule

Use `service.last_seen_at` as the source of truth.

A service is considered inactive when either condition is true:

- `last_seen_at` is `null`
- `Date.now() - Date.parse(last_seen_at)` is greater than `inactiveDays * 24 * 60 * 60 * 1000`

`inactiveDays` comes from `VITE_SERVICE_INACTIVE_DAYS`.

Config parsing rules:

- empty value uses default `3`
- non-numeric or non-positive value uses default `3`
- numeric integer or decimal values greater than `0` are accepted

## List Behavior

The current visible list pipeline remains:

1. fetch services
2. apply environment filter
3. apply source-kind filter
4. apply search filter

Then add one new step:

5. partition filtered services into active and inactive groups

Ordering rules:

- active services keep the existing `compareServicesForSurface` sort
- inactive services also use `compareServicesForSurface`
- the inactive group is always rendered after the active group

## Presentation

### Main Table

Render only active services in the current table body.

If there are no active services but there are inactive services, do not render the main table card. Render only the collapsed inactive section below the summary area.

### Inactive Section

Render a collapsed `<details>` block below the active table area.

Summary content:

- title: `Long inactive services (N)` / `长期未活跃服务（N）`
- helper copy: short explanation that these services have had no activity beyond the configured threshold

Body content:

- reuse the existing row presentation for inactive services rather than inventing a second visual treatment

Default state:

- collapsed

## Copy

Add i18n strings for:

- inactive section title
- inactive section description

Reuse existing last activity cell formatting. No new per-row badge is required in this iteration.

## Testing

Add coverage for:

1. a service seen recently stays in the main list
2. a service older than the default threshold appears in the collapsed inactive section
3. the inactive section shows the correct count
4. invalid or missing env config falls back to `3`

Tests should remain unit-level in Vitest. No Playwright coverage is required for this change.

## Risks And Mitigations

### Time-sensitive tests

Risk:

- tests that depend on the current clock can become flaky

Mitigation:

- freeze time in the page test or construct timestamps relative to a fixed mocked now

### Empty active list state

Risk:

- rendering assumptions may expect at least one row in the main table

Mitigation:

- keep conditional rendering explicit for active and inactive groups and verify the all-inactive case if the JSX path needs it

## Success Criteria

1. With default config, any service whose `last_seen_at` is more than `3` days old is not shown in the main visible rows.
2. Those services remain reachable in a collapsed section at the bottom of the page.
3. Changing `VITE_SERVICE_INACTIVE_DAYS` changes the grouping threshold without code changes.
4. Existing search and source-kind filtering still apply before grouping.

# OneStep Control Plane Nothing Signal Console Design

Date: 2026-05-16
Status: Ready for user review
Owner: Codex

## Summary

Redesign the control-plane console entry surfaces in a Nothing-inspired `light` theme using the `Signal Console` direction. Scope is limited to:

- `frontend/src/components/layout/AppShell.tsx`
- `frontend/src/pages/services-list/ServicesListPage.tsx`
- `frontend/src/pages/service-detail/ServiceDetailPage.tsx`

The redesign is visual plus light structural reordering. It does not change routes, APIs, data contracts, query behavior, or feature scope.

## Design Inputs

The following decisions were validated in conversation:

- Theme mode: `light`
- Density: `middle` between operator efficiency and showcase polish
- Restructure level: `light` reordering only
- Chosen structural approach: `Signal Console`
- Initial rollout scope: `AppShell`, services list, service detail only

## Goals

1. Make the console feel intentional and branded rather than generic or Apple-like.
2. Improve first-glance readability for service health and operator attention signals.
3. Preserve operational scanning efficiency for lists and detail tables.
4. Unify shell, typography, hierarchy, and status emphasis across the three scoped surfaces.

## Non-Goals

- No dark mode in this iteration
- No changes to login, task detail, instance detail, or notification settings pages
- No backend or query-layer changes
- No feature additions
- No route or IA expansion beyond light section reordering
- No broad rewrite of shared UI components across the full app

## Fonts

The redesign requires these Google Fonts:

- `Space Grotesk` for body and UI copy
- `Space Mono` for labels, metadata, and numeric/operator surfaces
- `Doto` for the single hero moment per page

Load via Google Fonts in the frontend stylesheet layer. The design assumes these fonts are not currently available and must be added explicitly.

## Visual System

### Theme

Use a Nothing-inspired light system:

- Page background: warm off-white, not flat pure white
- Surfaces: white or slightly raised off-white
- Borders: light neutral gray, visible but subtle
- Primary text: near-black
- Secondary text: medium gray
- Disabled text: softer gray
- Accent: `#D71921`

No shadows, no glassmorphism, no blur, no Apple glow, and no decorative gradients in UI chrome. Background atmosphere can use very subtle radial texture, but core surfaces stay flat and mechanical.

### Typography

Per page, stay within:

- 2 primary font families in routine UI: `Space Grotesk`, `Space Mono`
- `Doto` only for the hero metric or hero service signal
- 3 effective type tiers per page:
  - Hero display
  - Body/subheading
  - Monospace labels/meta

### Hierarchy

Each scoped page must preserve exactly three visual layers:

1. Primary: one oversized signal
2. Secondary: runtime summaries, tables, operational panels
3. Tertiary: navigation, environment labels, timestamps, version strings, helper text

## Page-Level Design

### AppShell

Current shell behavior remains unchanged. Only presentation changes.

New shell intent:

- A thin top instrument bar rather than a product-marketing nav
- Brand at left, primary nav centered or adjacent, session/logout controls at right
- `Space Mono` uppercase nav labels
- Active nav indicated by a restrained marker such as underline or accent dot, not a large filled pill

Shell content rules:

- Brand remains `OneStep`
- Keep existing nav destinations
- Keep existing logout/logout-all actions
- Do not add dashboard summary data to the shell

### Services List

The page becomes:

1. Page intro with title and restrained subtitle
2. One primary hero signal: total visible services
3. Compact summary band: online instance coverage, fully ready services, attention services, total visible instances
4. Inline controls: environment filter and search
5. Source-kind filter chips if available
6. Dense services table

Rules:

- The hero exists to establish hierarchy, not to replace the table
- The table remains the main operational surface
- Status emphasis comes from typography, spacing, and sparse accent usage
- Avoid card-grid treatment for service rows

Services table priorities:

- Service name is the strongest row anchor
- Environment and supporting tags are tertiary
- Online coverage remains machine-readable
- Last activity and deployment remain visible without opening the service

### Service Detail

Keep the existing four views:

- `overview`
- `instances`
- `tasks`
- `commands`

Restructure the first screen for faster triage:

1. Back control
2. Service title and status
3. Compact action area for lookback and command triggers
4. Left rail tab navigation with lighter visual weight
5. `overview` starts with signal-first content

Overview order:

1. Service headline and status
2. One key service signal block
3. Compact summary band
4. High-value information blocks:
   - service metadata
   - runtime signals
   - topology/command/capability summaries already present in current data

Rules:

- Do not remove any existing core information
- Re-rank it so operators can answer “healthy or not” before reading detail tables
- Keep instances, tasks, and commands as table-oriented views
- Keep advanced or lower-frequency controls in collapsible areas

## Component Rules

### Buttons and Controls

- Button labels use `Space Mono`, uppercase
- Prefer outline, ghost, or restrained solid treatments
- Destructive action uses accent red border/text
- Filters and search stay inline, compact, and technical

### Tabs and Side Rail

- Side rail in service detail should feel like a thin instrument selector
- Active state is text/border/accent only
- Do not introduce icon-heavy navigation

### Tables

- Preserve table density and scan behavior
- No zebra striping
- Use dividers, alignment, and text contrast to structure rows
- Numeric/status values should be easier to scan than descriptive text

### Status Color

- Red only for real interruption: attention, destructive actions, drift/error emphasis
- Success/warning colors may tint values but not full row backgrounds
- Most hierarchy should still work in grayscale

### Empty and Loading States

- No skeletons
- Use existing functional behavior, but if touched visually prefer restrained inline states such as `[LOADING]`
- Empty states remain minimal and non-illustrative

## Implementation Boundaries

Implementation should prefer surgical changes:

- Reuse the current React structures where possible
- Only make JSX changes that serve hierarchy or layout improvements
- Introduce a scoped Nothing-style stylesheet layer rather than mutating the Apple workbench aesthetic further
- Avoid renaming broad class systems unless necessary for clarity inside the touched surfaces

Acceptable JSX adjustments:

- Wrapping existing blocks to create stronger hierarchy
- Reordering existing sections within the same page
- Adding small semantic containers for hero, summary band, or rail presentation

Out of bounds:

- New data fetching
- New global state
- New generic component architecture for the full application

## Testing and Validation

The redesign is complete when all of the following are true:

1. A user can identify total services and attention count within roughly 3 seconds on the services page.
2. A user can identify service health posture, topology drift risk, and whether more drilling is needed within roughly 5 seconds on service detail.
3. The three scoped surfaces share one visual language and no longer read as Apple-inspired UI.
4. Existing interactions continue to work:
   - environment filter
   - search
   - source-kind chips
   - service detail tabs
   - lookback control
   - quick actions
   - collapsible advanced sections
5. Narrow screens still preserve readable hierarchy and navigable overflow behavior.

## Risks

- Existing `legacy.css` and `apple-workbench.css` may create selector conflicts with the new layer.
- The current pages already mix multiple visual directions, so targeted JSX simplification may be needed to avoid “theme on top of theme”.
- Service detail contains dense sections; over-stylizing it would reduce operator speed. The implementation must keep the table-first behavior intact outside the overview hero.

## Recommended Delivery Shape

The implementation should land in three slices:

1. Global shell and font import
2. Services list hierarchy and table restyle
3. Service detail rail, overview hierarchy, and detail-table restyle

This is an implementation ordering recommendation, not a requirement to split commits.

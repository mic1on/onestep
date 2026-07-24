# Plane Vibehub Command Center Design

Date: 2026-06-18
Status: Ready for user review
Owner: Codex

## Summary

Redesign the OneStep Control Plane frontend into a full-console Vibehub-style
operator experience. The redesign changes the information architecture, default
route, page anatomy, and visual system, while preserving the current backend API
contracts and control-plane protocol semantics for the first implementation
pass.

The chosen direction is **Command Center / 动作中枢**: the console opens on one
shared operational home that answers "what needs attention now" across services,
instances, tasks, worker agents, deployments, workers, connectors, notification
coverage, and command stream freshness. Existing domain pages remain available
for deep inspection and editing, but they are regrouped under a clearer
Now / Deploy / Build / Admin navigation model.

## Design Inputs

The following decisions were validated in conversation:

- Scope: full console redesign, not a single page or light visual replacement.
- Restructure level: information architecture may change, including navigation
  grouping and default workflows.
- Primary user model: operators, builders, and admins are all supported; the
  home page should unify them with status overview plus next actions.
- Chosen IA direction: Command Center over Lifecycle Console or Role Workspaces.
- Page system: use one Vibehub page family for list pages, detail pages,
  workbench/editor pages, settings pages, and auth/system-state pages.
- Data strategy: first pass aggregates existing frontend queries; no new
  backend Command Center endpoint is required before implementation begins.
- Verification strategy: focused unit/component tests, Vite build, and visual QA
  across desktop and mobile.

## Goals

1. Make the console useful within the first viewport by surfacing current
   operational attention, not just navigation.
2. Unify all existing pages into one compact Vibehub design language: white
   canvas, warm-gray preview/work surfaces, low-radius cards, precise borders,
   orange-red action emphasis, and scan-first density.
3. Make services, tasks, instances, agents, deployments, workers, connectors,
   and notification settings feel like parts of one control surface.
4. Preserve existing backend API contracts, reporter payload contracts,
   WebSocket protocol behavior, and command semantics.
5. Keep risky operations permission-gated and reason-gated while making valid
   operator actions easier to discover.
6. Keep the redesign implementable as a frontend-first pass with clear follow-up
   options for backend aggregation if needed later.

## Non-Goals

- No reporter payload field changes.
- No WebSocket frame or runtime-control protocol changes.
- No backend schema changes in the first pass.
- No new command kinds, deployment states, connector types, or worker runtime
  behavior.
- No role-specific top-level apps; roles share the same IA and actions are
  gated by current session permissions.
- No marketing-style landing page, oversized hero, decorative gradient system,
  or nested card-heavy layout.
- No broad rewrite of query clients or mutation semantics solely for aesthetic
  reasons.

## Visual System

Use the Vibehub UI reference language as the canonical design system for the
redesign.

### Tokens

Start from the existing `vibe-reference-console.css` direction and normalize it
around these tokens:

```css
:root {
  --vh-bg: #ffffff;
  --vh-surface: #f7f7f8;
  --vh-surface-warm: #f4f0ea;
  --vh-surface-tint: #fff2ec;
  --vh-border: #e8e3dc;
  --vh-border-strong: #d8d2ca;
  --vh-text: #181010;
  --vh-text-soft: #5f5a55;
  --vh-text-muted: #9a958f;
  --vh-primary: #ff4d18;
  --vh-success: #208858;
  --vh-info: #2870d8;
  --vh-warning: #ff9a3d;
  --vh-danger: #e63b2e;
  --vh-radius-card: 8px;
  --vh-radius-control: 6px;
}
```

The current frontend has older theme layers (`legacy`, `apple-workbench`,
`nothing-signal-console`) plus the new Vibehub stylesheet. Implementation should
consolidate page-level behavior under the Vibehub layer without deleting
unrelated styles unless they are no longer referenced by the redesigned pages.

### Typography

- Font stack: `Inter`, `SF Pro Display`, `PingFang SC`, `Noto Sans SC`,
  `Microsoft YaHei`, system sans-serif.
- Page titles: compact display scale, not marketing hero scale. Target
  32-44px desktop, smaller but fixed-token on mobile.
- Section titles: 18-24px.
- Card/list titles: 13-15px.
- Metadata and category labels: 10-12px, muted, uppercase only where it improves
  scanning.
- Do not scale font size with viewport width.
- Letter spacing stays `0`.

### Layout Rules

- Shell uses a persistent left sidebar on desktop and a compact top/overlay nav
  treatment on small screens.
- Content remains dense: page padding 24-32px mobile, 28-48px desktop.
- Cards and controls use 8px or less radius except tiny filter/status chips.
- No cards inside cards. Page sections are unframed layouts or single-level
  panels; cards are for repeated items, modals, and concrete tools.
- Orange is an accent for active states, primary actions, focus rings, badges,
  progress, and important operational marks.
- Use subtle motion only for state feedback, table hover, modal/drawer entry,
  loading, and recently updated rows. Respect `prefers-reduced-motion`.

## Information Architecture

### Top-Level Navigation

Replace the current flat nav with four groups:

1. **Now / 现在**
   - Command Center
   - Services
   - Tasks
   - Instances

2. **Deploy / 部署**
   - Agents
   - Deployments

3. **Build / 构建**
   - Workers
   - Connectors

4. **Admin / 管理**
   - Notifications
   - Settings

`Command Center` becomes the default route. Existing routes should keep working
where possible. If a route changes, preserve redirects from the old entry points.

### Role Model

Viewer, operator, and admin users see the same IA. Action availability is
derived from the current session:

- Viewers can inspect status, details, histories, and settings in read-only
  mode.
- Operators can perform permitted runtime/deployment actions with existing
  command review and reason flows.
- Admins can manage notification settings and future administrative surfaces.

Do not fork the console into separate role workspaces in this pass.

## Command Center

### Purpose

Command Center is a triage board. It should answer:

- Which services or deployments need attention?
- Are worker agents online and capacity available?
- Are command/session streams fresh?
- Which actions are safe and available for the current user?
- Which build/admin gaps will affect deployability or observability?

### Layout

First viewport anatomy:

1. Compact page header:
   - Kicker: `Command Center / 动作中枢`
   - Title: `What needs attention now`
   - Short operational description
   - Scope filter and search

2. Summary strip:
   - Attention count
   - Online instance coverage
   - Active services
   - Failed/dead-letter task signal when available
   - Deployment count or non-terminal deployment count

3. Main workbench:
   - Attention queue table/list
   - Next actions panel

4. Lower signal panels:
   - Agent capacity
   - Build readiness
   - Notification coverage
   - Command/session stream freshness when useful

### Data Sources

Use existing query hooks first:

- `useServicesQuery`
- `useWorkerAgentsQuery`
- `useWorkerDeploymentsQuery`
- `useWorkersQuery`
- `useConnectorsQuery`
- command stream status from `useCommandStreamStatus`
- service/command/session detail queries only where the page already has enough
  context or when a focused expansion is opened

Do not eagerly fetch every service dashboard in the first viewport. Command
Center can derive first-pass signals from list APIs and link to detail pages for
deeper task and instance analysis. If this proves insufficient, a later backend
aggregation endpoint can be designed separately.

### Attention Queue

Represent each item with:

- `id`
- `kind`: service, task, instance, worker_agent, deployment, worker, connector,
  notification, stream
- `label`
- `environment` when available
- `severity`: critical, warning, info, ok
- `signal`: short human-readable issue
- `nextActionLabel`
- `href`
- `updatedAt` or freshness label
- optional `action` descriptor if the current role can act directly

Initial derivation rules:

- Service with `service_status === "attention"` or low instance coverage:
  warning/critical item linking to service detail.
- Worker agent with `status !== "online"`: warning item linking to agent detail.
- Worker deployment with `observed_status` failed/cancelled/stopping/stopped
  unexpectedly or non-terminal for too long: item linking to deployment events.
- Worker with draft/not-ready status: info/warning item linking to worker editor.
- Connector catalog with missing configured types: info item only when it blocks
  known workers or user filters into Build readiness.
- Notification coverage gaps: info/warning item in Admin context, not a blocking
  incident unless configured as required later.
- Command stream stale/error: warning item linking to relevant service or global
  state explanation.

Keep severity structural, not color-only: pair color with labels, badges, and
row grouping.

### Next Actions

The panel surfaces existing actions, never invents new runtime operations:

- Open service/task/instance/agent/deployment detail.
- Review and dispatch permitted service/task/instance commands using existing
  reason/review dialogs.
- Deploy a ready worker using the existing deployment flow.
- Open deployment event timeline.
- Open connector/settings editors.

Risky actions keep the existing destructive review dialog and reason
requirement. The Command Center may deep-link into the existing flow or open the
same dialog; it should not bypass command capability checks.

## Page System

### List Pages

Applies to:

- Services
- Agents
- Deployments
- Workers
- Connectors where table/catalog mode is appropriate

Anatomy:

1. Compact header with kicker, title, description, and primary action.
2. Inline filters/search.
3. Summary strip.
4. Dense table/list with stable columns.
5. Empty, filtered-empty, loading, stale, and error states.

Rules:

- Tables remain the main scanning surface.
- Status badges use the same component and tone map across pages.
- Long names wrap cleanly or truncate with accessible full values.
- Row hover may tint subtly; no zebra striping.

### Detail Pages

Applies to:

- Service detail
- Task detail
- Instance detail
- Agent detail
- Deployment event detail

Anatomy:

1. Back rail/control.
2. Title row with status badge.
3. Short subtitle/freshness line.
4. Hero metric or primary runtime signal.
5. Side or horizontal section nav.
6. Summary band.
7. Main tabbed/detail content.
8. Command feed, session feed, topology, metrics, or timeline panels as
   appropriate.

Rules:

- Answer "healthy or not?" before requiring table reading.
- Keep detail pages data-dense, not card galleries.
- Use the same lookback/filters pattern across service/task/instance pages.
- Existing command and deployment controls remain permission-aware.

### Workbench Pages

Applies to:

- Worker editor
- Connector editor
- Deploy dialog / deployment setup surfaces

Anatomy:

1. Left catalog, step rail, or entity list.
2. Right editable form/detail surface.
3. Validation/error area near the affected fields.
4. Primary action aligned predictably.
5. Review modal for risky or irreversible operations.

Rules:

- Use compact form labels and fields.
- Keep connector/worker catalogs practical; no decorative cards.
- Preserve keyboard flow and visible focus states.

### Settings Pages

Applies to:

- Notification settings now
- Future admin settings later

Anatomy:

1. Header and summary strip.
2. Provider/channel list.
3. Scope chips or service selector.
4. Edit/test controls.
5. Read-only viewer state.

Rules:

- Settings should feel operational: missing coverage can surface on Command
  Center.
- Test/send feedback uses existing toast/message patterns.

### Auth and System States

Applies to:

- Login
- Bootstrap required
- Auth check failed
- Permission-limited states
- Not found

Anatomy:

1. Brand rail.
2. Compact panel.
3. Clear status and next step.

Rules:

- No marketing landing page.
- Login remains utilitarian.
- Bootstrap-required should explain that an admin must be created before login.

## Shared Components

Prefer evolving current components over inventing a large new component library.
Useful shared primitives:

- `PageHeader` or renamed Vibehub-compatible header
- `SummaryStrip` / `SummaryChip`
- `StatusBadge`
- `DataTable`
- `FilterBar`
- `ActionQueue`
- `AttentionItem`
- `Panel`
- `EmptyState`
- `SegmentedControl`
- `CommandCenterPage`

Add abstractions only when at least two redesigned pages consume them or when
they remove meaningful duplication. Keep route/page-specific logic close to the
page when it is only used once.

## Data and State Model

### Query Strategy

The first implementation pass remains frontend-first:

- Reuse existing React Query hooks.
- Add small pure derivation helpers for Command Center attention items.
- Avoid adding backend endpoints until UI behavior proves the current data shape
  is insufficient.
- Preserve existing refetch intervals for live service and command/session
  views.
- Do not fetch all service dashboards by default from Command Center.

### Common UI States

Every redesigned page should account for:

- `loading`
- `empty`
- `filtered empty`
- `ready`
- `stale`
- `permission-limited`
- `error`

Use consistent placement:

- Loading appears inside the main content region.
- Error state appears near the failed surface and includes enough detail for
  debugging.
- Permission-limited states explain that actions are hidden or read-only due to
  role, without hiding the surrounding context.
- Stale stream states use banners or attention items, not silent disabled
  controls.

## Routing

Proposed route model:

- `/` -> Command Center
- `/services`
- `/services/:serviceName`
- `/services/:serviceName/tasks/:taskName`
- `/services/:serviceName/instances/:instanceId`
- `/agents`
- `/agents/:agentId`
- `/agents/:agentId/deployments/:deploymentId/events`
- `/deployments` if a global deployment list is added from existing deployment
  query support
- `/workers`
- `/workers/:workerId`
- `/connectors`
- `/settings/notifications`

If `/deployments` is not implemented in the first increment, keep Deploy group
with Agents as the primary entry and surface deployment timelines from agent
detail.

## Accessibility

- Use semantic navigation landmarks, buttons, forms, dialogs, tables, and links.
- Preserve keyboard order in shell, tables, dialogs, action menus, and forms.
- Add visible focus rings using the orange accent.
- Do not rely on color alone for status or severity.
- Dialogs need labels and focus management.
- Dense desktop controls should be at least 32px tall; touch layouts should use
  at least 40px targets.
- Respect `prefers-reduced-motion`.

## Implementation Boundaries

This redesign should be implemented in phases:

1. Foundation:
   - Normalize Vibehub token layer.
   - Update shell and route default.
   - Add Command Center page with client-side attention derivation.

2. Shared page system:
   - Introduce only the shared primitives needed by multiple pages.
   - Convert services, agents, workers, connectors, and settings list/workbench
     surfaces to the shared anatomy.

3. Detail pages:
   - Convert service, task, instance, agent, and deployment-event detail pages
     to the unified detail anatomy.

4. Polish and QA:
   - Responsive fixes.
   - Empty/error/stale/permission-limited states.
   - Visual QA and build/test cleanup.

Out of bounds for the first implementation pass:

- Backend Command Center API.
- Protocol or reporter changes.
- New RBAC model.
- New deployment command behavior.
- Full deletion of old style files unless usage is removed and verified.

## Testing and Verification

### Unit and Component Tests

Add focused tests for:

- Command Center attention derivation from service, agent, deployment, worker,
  connector, and stream inputs.
- Permission-limited next-action visibility.
- Empty/error/loading/stale states.
- Route default from `/` to Command Center.
- Shared shell navigation labels and active states.

Update existing page tests only where shared shell/header/page anatomy changes
behavior, accessible names, or visible state.

### Build and Manual QA

Run:

```bash
pnpm build
```

from `frontend`.

Manual QA should cover:

- Desktop shell and grouped navigation.
- Mobile/touch navigation behavior.
- Command Center first viewport.
- Services list and service detail.
- Task and instance detail with long names.
- Agents and deployment events.
- Workers and connectors workbench forms.
- Notification settings read-only and editable states.
- Login, bootstrap-required, auth-check, not-found, empty, error, stale, and
  permission-limited states.

### Protocol Validation

No backend protocol tests are required for this design pass because it does not
change reporter fields, WebSocket frames, runtime identity, or remote command
semantics. Existing backend tests remain relevant as regression coverage if any
implementation accidentally touches API behavior.

## Open Follow-Ups

- Decide during implementation whether `/deployments` ships in the first
  increment or remains reachable through agent detail and Command Center links.
- After the frontend-first pass is live, measure whether Command Center needs a
  backend aggregation endpoint to avoid multiple list queries or to expose richer
  task-level attention signals.
- Decide whether `.superpowers/` should be added to project `.gitignore`; the
  brainstorming mockups are local artifacts and should not be committed with
  product code.

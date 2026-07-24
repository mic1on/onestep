# Plane Mobile Responsive Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every authenticated plane surface usable below 1024px without changing APIs, routing contracts, or desktop behavior.

**Architecture:** `App.tsx` retains route and async-state ownership. A mobile-only bottom navigation component appears below `lg`, while dense desktop surfaces get responsive presentation variants. Existing handlers, pending states, errors, and toasts remain authoritative.

**Tech Stack:** React 19, TypeScript, Tailwind CSS 4, Lucide React, Vitest, Testing Library, Playwright.

---

## File Map

- Create: `frontend/src/components/MobileNavigation.tsx` and `MobileNavigation.test.tsx` for the mobile bar and More panel.
- Create: `frontend/src/components/InstancesTable.test.tsx` for mobile instance record coverage.
- Modify: `frontend/src/App.tsx`, `index.css`, `Sidebar.tsx`, and `i18n.tsx` for the shell.
- Modify: overview, services, tasks, instances, telemetry, chart, diagnostics, config, notifications, toast, and E2E files for scoped responsive presentation.

### Task 1: Add MobileNavigation

**Files:**
- Create: `frontend/src/components/MobileNavigation.tsx`
- Create: `frontend/src/components/MobileNavigation.test.tsx`
- Modify: `frontend/src/i18n.tsx`
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Add translations for the new controls**

Add the following keys to both existing locale maps:

```ts
'nav.more': 'More',
'nav.moreMenu': 'Open more menu',
'nav.closeMoreMenu': 'Close more menu',
```

The Chinese values are `更多`, `打开更多菜单`, and `关闭更多菜单`.

- [ ] **Step 2: Write failing component tests**

```tsx
it('navigates to services from the bottom bar', async () => {
  const user = userEvent.setup();
  const onViewChange = vi.fn();
  renderNavigation({ currentView: 'overview', onViewChange });
  await user.click(screen.getByRole('button', { name: 'Services' }));
  expect(onViewChange).toHaveBeenCalledWith('servicesList');
});

it('closes More with Escape and restores trigger focus', async () => {
  const user = userEvent.setup();
  renderNavigation({ currentView: 'overview' });
  const trigger = screen.getByRole('button', { name: 'Open more menu' });
  await user.click(trigger);
  await user.keyboard('{Escape}');
  expect(document.activeElement).toBe(trigger);
});
```

- [ ] **Step 3: Run the focused test**

Run: `pnpm --filter onestep-control-plane-ui test -- MobileNavigation.test.tsx`

Expected: FAIL because `MobileNavigation` does not exist.

- [ ] **Step 4: Implement the bottom navigation and More panel**

Use the same inputs as `Sidebar`: `currentView`, `isLogoutPending`, `onLogout`,
and `onViewChange`. Render Overview, Services, Notifications, and More inside a
fixed `lg:hidden` four-column `<nav>`. Services is current for both
`servicesList` and `services`. Each item uses a Lucide icon, `aria-current`, and
a 44px target.

```tsx
<nav className="fixed inset-x-0 bottom-0 z-30 grid h-[calc(4.5rem+env(safe-area-inset-bottom))] grid-cols-4 border-t border-slate-200 bg-white/95 px-2 pb-[env(safe-area-inset-bottom)] backdrop-blur lg:hidden">
  <button aria-current={currentView === 'overview' ? 'page' : undefined} className="ui-mobile-nav-item" onClick={() => onViewChange('overview')} type="button"><LayoutDashboard /><span>{t('nav.globalOverview')}</span></button>
  <button aria-current={servicesActive ? 'page' : undefined} className="ui-mobile-nav-item" onClick={() => onViewChange('servicesList')} type="button"><Server /><span>{t('nav.services')}</span></button>
  <button aria-current={currentView === 'notifications' ? 'page' : undefined} className="ui-mobile-nav-item" onClick={() => onViewChange('notifications')} type="button"><Bell /><span>{t('nav.notifications')}</span></button>
  <button ref={triggerRef} aria-expanded={isMoreOpen} aria-haspopup="dialog" aria-label={t('nav.moreMenu')} className="ui-mobile-nav-item" onClick={() => setIsMoreOpen((open) => !open)} type="button"><MoreHorizontal /><span>{t('nav.more')}</span></button>
</nav>
```

Use `useDismissibleMenu` for More. Its bottom panel has `role="dialog"`, a
backdrop button, `LocaleSwitcher`, existing Docs/GitHub anchors, logout, and a
44px close button. Close before external navigation or logout.

- [ ] **Step 5: Add exact mobile interaction utilities**

```css
.ui-mobile-nav-item { display:flex; min-width:44px; min-height:44px; flex-direction:column; align-items:center; justify-content:center; gap:2px; border-radius:8px; color:#64748b; font-size:10px; font-weight:700; }
@media (max-width: 1023px) { .ui-pressable:active:not(:disabled), .ui-mobile-nav-item:active:not(:disabled) { transform:scale(0.96); } }
```

Keep existing transitions property-specific; do not add `transition: all`.

- [ ] **Step 6: Verify and commit**

Run: `pnpm --filter onestep-control-plane-ui test -- MobileNavigation.test.tsx useDismissibleMenu.test.tsx`

Expected: PASS.

Commit: `git add frontend/src/components/MobileNavigation.tsx frontend/src/components/MobileNavigation.test.tsx frontend/src/i18n.tsx frontend/src/index.css && git commit -m "feat: add plane mobile navigation"`

### Task 2: Switch the shell at `lg`

**Files:**
- Modify: `frontend/src/App.tsx:990-1120`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/App.test.ts`

- [ ] **Step 1: Write a failing route helper test**

```ts
import { isServicesNavigationActive } from './App';

it('keeps service and task detail routes active in Services', () => {
  expect(isServicesNavigationActive('servicesList')).toBe(true);
  expect(isServicesNavigationActive('services')).toBe(true);
  expect(isServicesNavigationActive('overview')).toBe(false);
});
```

- [ ] **Step 2: Run the test**

Run: `pnpm --filter onestep-control-plane-ui test -- App.test.ts`

Expected: FAIL because the helper is absent.

- [ ] **Step 3: Implement the responsive shell and compact command row**

Export the helper, render `MobileNavigation` next to `Sidebar`, add
`hidden lg:flex` to the fixed desktop rail, and replace the current left offset:

```tsx
<div className="flex min-h-[100dvh] overflow-x-hidden bg-slate-50 font-sans text-slate-800">
  <Sidebar currentView={currentView} isLogoutPending={isLogoutPending} onLogout={() => void handleLogout()} onViewChange={navigateToView} />
  <MobileNavigation currentView={currentView} isLogoutPending={isLogoutPending} onLogout={() => void handleLogout()} onViewChange={navigateToView} />
  <div className="flex h-[100dvh] min-w-0 flex-1 flex-col overflow-hidden lg:ml-[240px]">
    <main className="flex-1 overflow-y-auto bg-slate-50 p-3 pb-[calc(5.5rem+env(safe-area-inset-bottom))] sm:p-5 sm:pb-[calc(5.5rem+env(safe-area-inset-bottom))] lg:p-6 lg:pb-6">
```

Hide `LocaleSwitcher` below `lg`. Keep API status and environment visible.
Refresh becomes a 44px icon button below `lg` and retains its text label above
that breakpoint.

- [ ] **Step 4: Verify and commit**

Run: `pnpm --filter onestep-control-plane-ui test -- App.test.ts MobileNavigation.test.tsx`

Expected: PASS.

Commit: `git add frontend/src/App.tsx frontend/src/components/Sidebar.tsx frontend/src/App.test.ts && git commit -m "feat: make plane shell responsive"`

### Task 3: Implement the approved task-detail treatment

**Files:**
- Modify: `frontend/src/App.tsx:1130-1520`
- Modify: `frontend/src/App.test.ts`

- [ ] **Step 1: Write a failing compact-metric contract test**

```ts
import { taskMetricCardClassName } from './App';

it('keeps task metrics compact before desktop', () => {
  expect(taskMetricCardClassName).toContain('items-center');
  expect(taskMetricCardClassName).toContain('lg:h-28');
});
```

- [ ] **Step 2: Run the test**

Run: `pnpm --filter onestep-control-plane-ui test -- App.test.ts`

Expected: FAIL because the class contract is absent.

- [ ] **Step 3: Add Play plus Ellipsis below `lg`**

Keep current labeled buttons inside `hidden lg:flex`. Add a mobile action cluster
with a 44px Play icon calling `handleOpenManualRun(selectedTask.id)` and an
Ellipsis icon using its own `useDismissibleMenu`. The menu contains restart and
pause/resume items, calls current handlers, preserves `disabled` and
`aria-busy`, closes after selection, and restores trigger focus.

```tsx
<div className="flex shrink-0 items-center lg:hidden">
  <button aria-label={tr('button.runOnce')} className="ui-pressable grid h-11 w-11 place-items-center rounded-md text-emerald-700 hover:bg-emerald-50" onClick={() => handleOpenManualRun(selectedTask.id)} type="button"><Play className="h-4 w-4" /></button>
  <button ref={taskActionTriggerRef} aria-expanded={isTaskActionMenuOpen} aria-haspopup="menu" aria-label={tr('button.moreActions', { name: selectedTask.name })} className="ui-pressable grid h-11 w-11 place-items-center rounded-md text-slate-500 hover:bg-slate-100" onClick={() => setIsTaskActionMenuOpen((open) => !open)} type="button"><MoreHorizontal className="h-5 w-5" /></button>
</div>
```

- [ ] **Step 4: Compact metrics and make the dialog a bottom panel on phones**

```ts
export const taskMetricCardClassName = 'flex min-h-[52px] min-w-0 items-center justify-between rounded-xl border border-slate-200 bg-white px-3 py-2 shadow-xs lg:h-28 lg:flex-col lg:items-start lg:justify-between lg:p-5';
```

Use `grid-cols-2 gap-2 lg:grid-cols-4 lg:gap-6`; hide secondary metric copy and
throughput progress below `lg`. Use `min-w-0 break-words` for long titles and
breadcrumbs. Below `sm`, render the manual-run dialog using `items-end`,
`rounded-t-xl`, `w-full`, and
`max-h-[calc(100dvh-env(safe-area-inset-bottom))]`; preserve the desktop dialog
with `sm:` classes.

- [ ] **Step 5: Verify and commit**

Run: `pnpm --filter onestep-control-plane-ui test -- App.test.ts TasksList.test.tsx`

Expected: PASS.

Commit: `git add frontend/src/App.tsx frontend/src/App.test.ts && git commit -m "feat: compact plane task detail on mobile"`

### Task 4: Adapt overview, service, and task records

**Files:**
- Modify: `frontend/src/components/OverviewPage.tsx`
- Modify: `frontend/src/components/ServicesList.tsx`
- Modify: `frontend/src/components/TasksList.tsx`
- Modify: `frontend/src/components/TelemetryStats.tsx`
- Modify: `frontend/src/components/ServicesList.test.tsx`
- Modify: `frontend/src/components/TasksList.test.tsx`

- [ ] **Step 1: Write failing record-layout tests**

```tsx
it('marks task row command menus desktop-only while preserving task selection', () => {
  renderTasksList(baseTask);
  expect(screen.getByRole('button', { name: /More actions for/ }).className).toContain('hidden');
  expect(screen.getByText('sync_user_record')).toBeTruthy();
});
```

Add a `ServicesList` assertion that the service title includes `break-words`.

- [ ] **Step 2: Run the tests**

Run: `pnpm --filter onestep-control-plane-ui test -- ServicesList.test.tsx TasksList.test.tsx`

Expected: FAIL because task menus are visible at every breakpoint and service
titles lack the narrow wrapping class.

- [ ] **Step 3: Implement responsive records**

Use these patterns in the touched components:

```tsx
className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:gap-6"
className="min-w-0 break-words text-sm font-bold text-slate-900 lg:truncate"
className="relative hidden lg:block"
```

In `OverviewPage`, use `grid-cols-2 lg:grid-cols-4` with
`min-h-[84px] p-3 lg:h-32 lg:p-4`; stack registry and activity below `lg`.
Apply the same compact summary geometry in `TelemetryStats`. Do not add mobile
commands to list cards or nested card surfaces.

- [ ] **Step 4: Verify and commit**

Run: `pnpm --filter onestep-control-plane-ui test -- ServicesList.test.tsx TasksList.test.tsx`

Expected: PASS.

Commit: `git add frontend/src/components/OverviewPage.tsx frontend/src/components/ServicesList.tsx frontend/src/components/ServicesList.test.tsx frontend/src/components/TasksList.tsx frontend/src/components/TasksList.test.tsx frontend/src/components/TelemetryStats.tsx && git commit -m "feat: adapt plane records for narrow screens"`

### Task 5: Add read-only mobile instance records

**Files:**
- Modify: `frontend/src/components/InstancesTable.tsx`
- Create: `frontend/src/components/InstancesTable.test.tsx`

- [ ] **Step 1: Write a failing mobile-record test**

```tsx
it('renders read-only mobile instance identity and status', () => {
  renderInstancesTable([instance({ uuid: '11111111-1111-1111-1111-111111111111' })]);
  expect(screen.getByTestId('mobile-instance-record-11111111-1111-1111-1111-111111111111')).toBeTruthy();
  expect(screen.getByText('worker-a.local')).toBeTruthy();
  expect(screen.queryByRole('button', { name: /restart/i })).toBeNull();
});
```

- [ ] **Step 2: Run the test**

Run: `pnpm --filter onestep-control-plane-ui test -- InstancesTable.test.tsx`

Expected: FAIL because no mobile record exists.

- [ ] **Step 3: Render cards below `lg` and retain the desktop table**

Share search, filtering, and pagination state. Render a `lg:hidden` record list
and wrap checkbox, bulk action, and table markup in `hidden lg:block`.

```tsx
<article data-testid={`mobile-instance-record-${inst.uuid}`} className="rounded-xl border border-slate-200 bg-white p-3 shadow-xs">
  <div className="flex min-w-0 items-center justify-between gap-3">
    <code className="min-w-0 break-all text-xs font-bold text-slate-900">{inst.uuid}</code>
    <InstanceStatusBadge instance={inst} />
  </div>
  <dl className="mt-3 grid grid-cols-2 gap-x-3 gap-y-2 text-xs">
    <InstanceFact label={t('instances.hostname')} value={inst.hostname} />
    <InstanceFact label={t('instances.nodeName')} value={inst.nodeName} />
    <InstanceFact label={t('instances.pid')} value={inst.pid} />
    <InstanceFact label={t('instances.version')} value={inst.version} />
  </dl>
</article>
```

Use only local `InstanceStatusBadge` and `InstanceFact` helpers. They do not
receive command callbacks. Make search, filter, and pagination controls at least
44px below `lg`, retaining desktop dimensions with `lg:` overrides.

- [ ] **Step 4: Verify and commit**

Run: `pnpm --filter onestep-control-plane-ui test -- InstancesTable.test.tsx`

Expected: PASS.

Commit: `git add frontend/src/components/InstancesTable.tsx frontend/src/components/InstancesTable.test.tsx && git commit -m "feat: add plane mobile instance records"`

### Task 6: Contain chart, diagnostics, config, notifications, and toasts

**Files:**
- Modify: `frontend/src/components/ResourceChart.tsx`
- Modify: `frontend/src/components/TaskEventDiagnostics.tsx`
- Modify: `frontend/src/components/ConfigEditor.tsx`
- Modify: `frontend/src/components/NotificationSettingsPage.tsx`
- Modify: `frontend/src/components/ToastViewport.tsx`
- Modify: `frontend/src/components/NotificationSettingsPage.test.tsx`

- [ ] **Step 1: Write a failing notification-row assertion**

```tsx
expect(screen.getByTestId(`notification-channel-${channel.id}`).className).toContain('grid-cols-1');
```

- [ ] **Step 2: Run the test**

Run: `pnpm --filter onestep-control-plane-ui test -- NotificationSettingsPage.test.tsx`

Expected: FAIL because the channel row has no stable test id or mobile record
class.

- [ ] **Step 3: Implement contained narrow surfaces**

- `ResourceChart`: use `h-[320px] sm:h-[360px] lg:h-[400px]`; wrap filters and use `min-h-11` below `lg`.
- `TaskEventDiagnostics`: retain event records; make presets and paging touch-sized, with any horizontal scroll confined to the preset group.
- `ConfigEditor`: add `min-w-0 max-w-full overflow-auto` to code/pre containers and immediate wrappers.
- `NotificationSettingsPage`: add `data-testid={`notification-channel-${channel.id}`}`; use `grid-cols-1 lg:grid-cols-[minmax(220px,1.05fr)_minmax(270px,1.55fr)_104px]`; stack controls after details; preserve all existing mutations.
- `ToastViewport`: move phone toasts above the fixed bar:

```tsx
className="fixed inset-x-4 bottom-[calc(5.5rem+env(safe-area-inset-bottom))] z-50 space-y-2.5 sm:bottom-6 sm:right-6 sm:left-auto sm:w-full sm:max-w-sm"
```

Add `const [isMobileEditorOpen, setIsMobileEditorOpen] = useState(false)` to
`NotificationSettingsPage`. The New and Edit handlers set it to `true`; Cancel
and successful save set it to `false`. Wrap the existing form in
`isMobileEditorOpen ? 'fixed inset-x-0 bottom-0 z-40 block max-h-[calc(100dvh-env(safe-area-inset-bottom))] overflow-y-auto rounded-t-xl bg-white shadow-2xl lg:static lg:z-auto lg:max-h-none lg:overflow-visible lg:rounded-xl lg:bg-transparent lg:shadow-none' : 'hidden lg:block'`.
Use `max-h-[calc(100dvh-env(safe-area-inset-bottom))]`, `rounded-t-xl`, a
backdrop close button, and an independently scrolling form body below `sm`.
Keep the form's current desktop grid placement and all existing form state.

- [ ] **Step 4: Verify and commit**

Run: `pnpm --filter onestep-control-plane-ui test -- NotificationSettingsPage.test.tsx TaskEventDiagnostics.test.tsx ResourceChart.test.tsx ToastViewport.test.tsx`

Expected: PASS.

Commit: `git add frontend/src/components/ResourceChart.tsx frontend/src/components/TaskEventDiagnostics.tsx frontend/src/components/ConfigEditor.tsx frontend/src/components/NotificationSettingsPage.tsx frontend/src/components/NotificationSettingsPage.test.tsx frontend/src/components/ToastViewport.tsx && git commit -m "feat: adapt plane supporting surfaces for mobile"`

### Task 7: Verify responsive behavior end to end

**Files:**
- Modify: `frontend/e2e/app.spec.ts`
- Modify: `frontend/playwright.local.config.ts`

- [ ] **Step 1: Add phone and tablet projects**

```ts
projects: [
  { name: 'phone-360', use: { ...baseConfig.use, viewport: { width: 360, height: 800 } } },
  { name: 'phone-390', use: { ...baseConfig.use, viewport: { width: 390, height: 844 } } },
  { name: 'tablet-768', use: { ...baseConfig.use, viewport: { width: 768, height: 1024 } } },
]
```

- [ ] **Step 2: Add mobile overflow and task-detail coverage**

```ts
async function expectNoPageOverflow(page: Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  expect(overflow).toBe(false);
}

test('mobile navigation and task detail stay within the viewport', async ({ page }) => {
  await installApiMocks(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Services' }).click();
  await page.getByText('billing-sync').first().click();
  await page.getByText('orders_to_ledger').first().click();
  await expectNoPageOverflow(page);
  await expect(page.getByRole('button', { name: 'Run once' })).toBeVisible();
});
```

```ts
test('mobile instance records expose no row commands', async ({ page }) => {
  await installApiMocks(page);
  await page.goto('/');
  await page.getByRole('button', { name: 'Services' }).click();
  await page.getByText('billing-sync').first().click();
  await page.getByRole('button', { name: 'Instances' }).click();
  await expect(page.getByTestId('mobile-instance-record-11111111-1111-1111-1111-111111111111')).toBeVisible();
  await expect(page.getByTitle('Restart instance')).toHaveCount(0);
  await expectNoPageOverflow(page);
});

test('mobile manual run panel remains inside the viewport', async ({ page }) => {
  await installApiMocks(page);
  await page.goto('/services/billing-sync/prod/tasks/orders_to_ledger');
  await page.getByRole('button', { name: 'Run once' }).click();
  const box = await page.getByRole('dialog', { name: 'Run task once' }).boundingBox();
  expect(box).not.toBeNull();
  expect(box!.y).toBeGreaterThanOrEqual(0);
  expect(box!.y + box!.height).toBeLessThanOrEqual(800);
});
```

- [ ] **Step 3: Run targeted E2E tests**

Run: `pnpm --filter onestep-control-plane-ui e2e -- --config playwright.local.config.ts --project phone-360`

Expected: PASS with no document overflow.

- [ ] **Step 4: Run full frontend verification**

Run: `pnpm --filter onestep-control-plane-ui test`

Expected: PASS.

Run: `pnpm --filter onestep-control-plane-ui build`

Expected: TypeScript succeeds and Vite emits `dist/`.

- [ ] **Step 5: Rebuild and verify the control-plane container**

Run: `docker compose build plane`

Run: `docker compose up -d plane`

Run: `docker compose ps`

Expected: `plane` reports `healthy` before final browser verification.

- [ ] **Step 6: Capture screenshots and commit test coverage**

Inspect overview, service detail, task detail, instances, logs, notifications,
and manual run at 360px, 390px, 768px, and desktop. Verify no clipping,
overlap, bottom-bar occlusion, or desktop rail regression.

Commit: `git add frontend/e2e/app.spec.ts frontend/playwright.local.config.ts && git commit -m "test: cover plane mobile responsive workflows"`

## Plan Self-Review

- Spec coverage: Tasks 1-2 implement the shell, safe area, More panel, and compact command bar. Task 3 implements the approved task-detail action and metric treatment. Tasks 4-6 cover every remaining authenticated page family. Task 7 covers responsive and desktop verification, build, and container health.
- Placeholder scan: every task has exact files, a focused test, implementation constraints, and commands.
- Type consistency: `MobileNavigation` uses existing `ControlPlaneView`, locale, logout, and route callbacks. Task commands retain existing handlers and pending state.

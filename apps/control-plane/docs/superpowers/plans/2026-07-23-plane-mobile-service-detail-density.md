# Mobile Service Detail Density Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compact the service-detail page on phones by relocating status and counts into the breadcrumb and tab strip, while preserving the current desktop layout.

**Architecture:** `App.tsx` owns the context bar, service header, and tab strip. It will render a service-only mobile status dot after the breadcrumb name, hide the service header's text badge below `sm`, and read tab counts directly from `selectedService`. `TelemetryStats.tsx` will hide only the duplicate instance card below `sm` and compact the retained cards with responsive utilities.

**Tech Stack:** React 19, TypeScript, Tailwind CSS v4, Playwright, Vitest, Vite.

---

## File Structure

- Modify: `frontend/e2e/app.spec.ts` - mobile and desktop regression coverage using the existing API mocks.
- Modify: `frontend/src/App.tsx` - context spacing, mobile status dot, and task/instance tab counts.
- Modify: `frontend/src/components/TelemetryStats.tsx` - mobile-only duplicate-card removal and compact telemetry.

### Task 1: Add Failing Cross-Breakpoint Tests

**Files:**
- Modify: `frontend/e2e/app.spec.ts:548-560`

- [ ] **Step 1: Add these tests after `renders the control plane dashboard`**

```ts
test.describe("service detail density", () => {
  test.describe("on mobile", () => {
    test.use({ viewport: { width: 390, height: 844 } });

    test("moves service state and counts into compact affordances", async ({ page }) => {
      await installApiMocks(page);
      await page.goto("/");
      await page.getByText("billing-sync").first().click();

      const contextBarBox = await page.getByTestId("global-context-bar").boundingBox();
      expect(contextBarBox?.height).toBeLessThanOrEqual(64);

      const serviceName = page.getByTestId("service-breadcrumb-name");
      const statusDot = page.getByTestId("mobile-service-status");
      await expect(serviceName).toHaveText("billing-sync / prod");
      await expect(statusDot).toHaveAttribute("title", "RUNNING");
      const [serviceNameBox, statusDotBox] = await Promise.all([serviceName.boundingBox(), statusDot.boundingBox()]);
      expect(statusDotBox?.x).toBeGreaterThanOrEqual((serviceNameBox?.x ?? 0) + (serviceNameBox?.width ?? 0));
      await expect(page.getByTestId("service-header-status")).toBeHidden();

      await expect(page.getByRole("button", { name: "Tasks 2" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Instances 2" })).toBeVisible();
      await page.getByRole("button", { name: "Instances 2" }).click();
      await expect(page).toHaveURL(/\\?tab=instances$/);

      await expect(page.getByTestId("telemetry-total-instances")).toBeHidden();
      const health = page.getByTestId("telemetry-health");
      const throughput = page.getByTestId("telemetry-throughput");
      await expect(health).toBeVisible();
      await expect(throughput).toBeVisible();
      expect((await health.boundingBox())?.height).toBeLessThanOrEqual(128);
      expect((await throughput.boundingBox())?.height).toBeLessThanOrEqual(128);
    });
  });

  test.describe("on desktop", () => {
    test.use({ viewport: { width: 1280, height: 900 } });

    test("keeps the text badge and all telemetry cards", async ({ page }) => {
      await installApiMocks(page);
      await page.goto("/");
      await page.getByText("billing-sync").first().click();

      await expect(page.getByTestId("mobile-service-status")).toBeHidden();
      await expect(page.getByTestId("service-header-status")).toContainText("RUNNING");
      await expect(page.getByTestId("telemetry-total-instances")).toBeVisible();
      await expect(page.getByRole("button", { name: "Tasks 2" })).toBeVisible();
      await expect(page.getByRole("button", { name: "Instances 2" })).toBeVisible();
    });
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && pnpm exec playwright test e2e/app.spec.ts --grep "service detail density"`

Expected: FAIL because the status and telemetry test ids, mobile tab counts, and responsive visibility rules do not exist.

### Task 2: Update The Existing App Layout

**Files:**
- Modify: `frontend/src/App.tsx:1004-1086`
- Modify: `frontend/src/App.tsx:1144-1210`
- Modify: `frontend/src/App.tsx:1481-1495`

- [ ] **Step 1: Compact only the context-bar spacing**

Replace the context-bar opening element. It preserves `px-3 py-2` and the existing `h-11 w-11` refresh target.

```tsx
<div
  data-testid="global-context-bar"
  className="mx-auto mb-3 flex max-w-7xl flex-wrap items-center justify-between gap-2 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-semibold text-slate-600 shadow-xs sm:mb-4 sm:px-4"
>
```

- [ ] **Step 2: Replace only the no-task breadcrumb name with the status-dot group**

```tsx
{selectedTask ? (
  <button
    type="button"
    onClick={() => navigateToService(selectedServiceId)}
    className="rounded-sm text-slate-800 font-bold hover:text-indigo-600 focus:outline-hidden focus:ring-2 focus:ring-indigo-500 focus:ring-offset-2 transition-colors"
  >
    {selectedService.name}
  </button>
) : (
  <span className="flex min-w-0 items-center gap-2">
    <span data-testid="service-breadcrumb-name" className="min-w-0 truncate text-slate-800 font-bold">
      {selectedService.name}
    </span>
    <span data-testid="mobile-service-status" title={headerStatus.label} className="inline-flex shrink-0 sm:hidden">
      <span aria-hidden="true" className={`h-2 w-2 rounded-full ${headerStatus.dotClassName}`} />
      <span className="sr-only">{headerStatus.label}</span>
    </span>
  </span>
)}
```

Change the current heading badge to keep the task-detail text badge at every width and the service badge at `sm` and above:

```tsx
<span
  data-testid="service-header-status"
  className={`${headerStatus.className} ${selectedTask ? 'flex' : 'hidden sm:flex'} items-center gap-1 rounded-md border px-2 py-0.5 text-[10px] font-bold tracking-wide`}
>
  <span className={`h-1.5 w-1.5 rounded-full ${headerStatus.dotClassName}`} />
  {headerStatus.label}
</span>
```

- [ ] **Step 3: Replace the `SERVICE_TABS.map` callback with count-aware markup**

```tsx
{SERVICE_TABS.map((tab) => {
  const count = tab === 'Tasks' ? selectedService.totalTaskCount : tab === 'Instances' ? selectedService.totalInstances : null;
  const isActive = activeTab === tab;

  return (
    <button
      key={tab}
      onClick={() => navigateToServiceTab(tab)}
      className={`ui-pressable inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-xs font-bold ${
        isActive ? 'bg-indigo-600 text-white shadow-xs' : 'text-slate-500 hover:bg-slate-50 hover:text-slate-800'
      }`}
    >
      <span>{serviceTabLabel(tab, tr)}</span>
      {count !== null && (
        <span className={`min-w-4 rounded-full px-1.5 py-0.5 text-[10px] leading-none ${isActive ? 'bg-white/20 text-white' : 'bg-slate-100 text-slate-600'}`}>
          {count}
        </span>
      )}
    </button>
  );
})}
```

- [ ] **Step 4: Run the tests again**

Run: `cd frontend && pnpm exec playwright test e2e/app.spec.ts --grep "service detail density"`

Expected: status, context-bar, and count assertions pass. Telemetry assertions still fail because `TelemetryStats` has not yet changed.

### Task 3: Compact Mobile Telemetry Without Changing Desktop

**Files:**
- Modify: `frontend/src/components/TelemetryStats.tsx:17-120`

- [ ] **Step 1: Replace the instance-card opening tag**

```tsx
<div
  data-testid="telemetry-total-instances"
  className="relative hidden h-32 flex-col justify-between overflow-hidden rounded-xl border border-slate-200 bg-white p-3 shadow-xs sm:flex sm:h-40 sm:p-5"
>
```

Keep every existing child and calculation inside this card unchanged.

- [ ] **Step 2: Apply these opening tags to the existing health and throughput cards**

```tsx
<div
  data-testid="telemetry-health"
  className="relative flex h-32 flex-col justify-between overflow-hidden rounded-xl border border-slate-200 bg-white p-3 shadow-xs sm:h-40 sm:p-5"
>
```

```tsx
<div
  data-testid="telemetry-throughput"
  className="group relative flex h-32 flex-col justify-between overflow-hidden rounded-xl border border-slate-200 bg-white p-3 shadow-xs sm:h-40 sm:p-5"
>
```

For both retained cards, change labels to `text-[10px] ... sm:text-xs`, icons to `h-4 w-4 sm:h-5 sm:w-5`, metric values to `text-3xl ... sm:text-4xl`, metric margins to `mt-1.5 sm:mt-2`, and supporting text to `text-[11px] sm:text-xs`. Keep the throughput waveform unchanged.

- [ ] **Step 3: Run the regression test to verify it passes**

Run: `cd frontend && pnpm exec playwright test e2e/app.spec.ts --grep "service detail density"`

Expected: PASS. At 390px the instance card is hidden and the two retained cards are at most 128px high; at 1280px all three cards and the text badge remain visible.

### Task 4: Validate, Rebuild, And Commit

**Files:**
- Modify: `frontend/e2e/app.spec.ts`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/TelemetryStats.tsx`

- [ ] **Step 1: Run focused coverage**

Run: `cd frontend && pnpm exec vitest run src/App.test.ts`

Expected: PASS.

Run: `cd frontend && pnpm exec playwright test e2e/app.spec.ts --grep "renders the control plane dashboard|loads API-backed service|service detail density"`

Expected: PASS. Existing service navigation and task-detail breadcrumb checks remain unchanged.

- [ ] **Step 2: Build the frontend**

Run: `cd frontend && pnpm build`

Expected: TypeScript checks and Vite production build pass.

- [ ] **Step 3: Rebuild and start the control-plane image**

Run these commands from the repository root:

```bash
docker compose build plane
docker compose up -d plane
docker compose ps plane
```

Expected: the image rebuild completes, the `plane` container is running and healthy, and it serves the updated frontend.

- [ ] **Step 4: Verify the rendered page at both target widths**

At 390px, open API-backed `billing-sync / prod` and verify one compact context row, a dot immediately after its breadcrumb service name, tappable `Tasks 2` and `Instances 2` tabs, and two non-overlapping telemetry cards. At 1280px, verify the textual `RUNNING` badge and three telemetry cards remain.

- [ ] **Step 5: Commit only the feature files**

```bash
git add frontend/e2e/app.spec.ts frontend/src/App.tsx frontend/src/components/TelemetryStats.tsx
git commit -m "feat: compact mobile service detail"
```

Expected: one implementation commit containing only this feature and its regression test.

## Self-Review

- Spec coverage: Task 2 covers the context bar, service-only status dot, unchanged task-detail badge, and count badges. Task 3 covers mobile telemetry removal and compaction. Task 4 validates both breakpoints and performs the required image rebuild.
- Placeholder scan: no deferred work markers or unspecified test cases remain.
- Type consistency: counts use existing `Service.totalTaskCount` and `Service.totalInstances`; status uses existing `headerStatus`; no new types, API fields, or translations are introduced.

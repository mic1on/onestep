# Control Plane Nothing Signal Console Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor the control-plane shell, services list, and service detail pages into the approved Nothing-inspired light `Signal Console` direction without changing behavior.

**Architecture:** Keep existing React data flow and routing intact, add a scoped stylesheet layer that overrides the current Apple-like presentation, and make only the JSX changes needed to create the new hierarchy. Preserve table-first operational surfaces while introducing a single hero signal and a lighter navigation rail.

**Tech Stack:** React, TypeScript, React Router, TanStack Query, Vitest, Testing Library, CSS

---

### Task 1: Add the scoped Signal Console style layer

**Files:**
- Modify: `frontend/src/styles.css`
- Create: `frontend/src/styles/nothing-signal-console.css`

- [ ] **Step 1: Add the new stylesheet import after the existing theme imports**

```css
@import "./styles/foundation/index.css";
@import "./styles/legacy.css";
@import "./styles/apple-workbench.css";
@import "./styles/nothing-signal-console.css";
```

- [ ] **Step 2: Create the Signal Console token and page-scope overrides**

```css
@import url("https://fonts.googleapis.com/css2?family=Doto:wght@400;500;600;700&family=Space+Grotesk:wght@400;500;700&family=Space+Mono:wght@400;700&display=swap");

:root {
  --nd-bg: #f5f5f3;
  --nd-surface: #ffffff;
  --nd-surface-muted: #f0f0ed;
  --nd-border: #d8d8d2;
  --nd-text: #111111;
  --nd-text-secondary: #666666;
  --nd-text-disabled: #999999;
  --nd-accent: #d71921;
}
```

- [ ] **Step 3: Add scoped overrides for the three approved surfaces**

```css
.app-shell,
.page-shell,
.ref-console-page,
.ref-detail-page {
  font-family: "Space Grotesk", sans-serif;
}

.shell-nav-link,
.shell-logout-btn,
.ref-summary-chip span,
.ref-side-nav-item,
.ref-inline-control span {
  font-family: "Space Mono", monospace;
  text-transform: uppercase;
  letter-spacing: 0.08em;
}
```

- [ ] **Step 4: Run a CSS/type smoke check**

Run: `pnpm --dir frontend vitest run frontend/src/components/layout/AppShell.test.tsx`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add frontend/src/styles.css frontend/src/styles/nothing-signal-console.css
git commit -m "feat: add signal console style layer"
```

### Task 2: Rebuild the shell and services list hierarchy

**Files:**
- Modify: `frontend/src/components/layout/AppShell.tsx`
- Modify: `frontend/src/pages/services-list/ServicesListPage.tsx`
- Create: `frontend/src/pages/services-list/ServicesListPage.test.tsx`
- Test: `frontend/src/components/layout/AppShell.test.tsx`

- [ ] **Step 1: Write the failing services list hierarchy test**

```tsx
it("shows the signal-console hero and summary band", async () => {
  mockUseServicesQuery.mockReturnValue({
    data: { items: [buildService()], total: 1, limit: 100, offset: 0, source_kind_counts: {} },
    isPending: false,
    error: null,
  });

  renderPage();

  expect(await screen.findByText("1")).toBeInTheDocument();
  expect(screen.getByText(/live services/i)).toBeInTheDocument();
  expect(screen.getByText(/attention/i)).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the new test to confirm the current page does not satisfy it**

Run: `pnpm --dir frontend vitest run src/pages/services-list/ServicesListPage.test.tsx`
Expected: FAIL because the hero signal classes/content do not exist yet

- [ ] **Step 3: Update `AppShell.tsx` to match the approved shell hierarchy**

```tsx
<header className="shell-topbar">
  <div className="shell-topbar-main">
    <NavLink className="shell-brand" to="/services?environment=all">
      <span className="shell-brand-mark" aria-hidden="true">01</span>
      <span className="shell-brand-copy">
        <strong className="shell-brand-title">OneStep</strong>
        <span className="shell-brand-subtitle">Control Plane</span>
      </span>
    </NavLink>
    <nav className="shell-nav" aria-label={t("app.primaryNavAriaLabel")}>
      <NavLink className={({ isActive }) => isActive ? "shell-nav-link active" : "shell-nav-link"} to="/services?environment=all">
        {t("app.servicesNav")}
      </NavLink>
    </nav>
  </div>
</header>
```

- [ ] **Step 4: Restructure `ServicesListPage.tsx` around a single hero, a summary band, and the existing table**

```tsx
<div className="ref-console-page ref-services-page">
  <section className="signal-console-hero">
    <div className="signal-console-hero-copy">
      <span className="signal-console-kicker">Primary focus</span>
      <div className="signal-console-metric">
        <strong>{sortedItems.length}</strong>
        <span>{isZh ? "LIVE SERVICES" : "LIVE SERVICES"}</span>
      </div>
    </div>
    <div className="ref-page-actions">
      <label className="ref-inline-control ref-inline-control-select">
        <span>{t("servicesList.filterScope")}</span>
        <select onChange={(event) => updateSearchParam("environment", event.target.value)} value={selectedEnvironment}>
          <option value="all">{t("environment.all")}</option>
          <option value="prod">{t("environment.prod")}</option>
          <option value="staging">{t("environment.staging")}</option>
          <option value="dev">{t("environment.dev")}</option>
        </select>
      </label>
      <label className="ref-inline-control ref-inline-control-search">
        <span>{t("common.search")}</span>
        <input
          name="service-search"
          onChange={(event) => {
            const nextValue = event.target.value;
            setSearch(nextValue);
            startTransition(() => updateSearchParam("q", nextValue || undefined));
          }}
          type="search"
          value={search}
        />
      </label>
    </div>
  </section>

  <section className="ref-summary-strip signal-console-summary-band">
    <SummaryChip label={t("servicesList.summaryOnline")} tone="success" value={`${onlineInstances}/${totalInstances || 0}`} />
    <SummaryChip label={t("servicesList.summaryReady")} tone="accent" value={String(sortedItems.filter(isServiceFullyOnline).length)} />
    <SummaryChip label={t("servicesList.summaryAttention")} tone={attentionCount > 0 ? "danger" : "default"} value={String(attentionCount)} />
    <SummaryChip label={t("servicesList.summaryServices")} tone="default" value={String(sortedItems.length)} />
  </section>
</div>
```

- [ ] **Step 5: Run shell and services list tests**

Run: `pnpm --dir frontend vitest run src/components/layout/AppShell.test.tsx src/pages/services-list/ServicesListPage.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/layout/AppShell.tsx frontend/src/pages/services-list/ServicesListPage.tsx frontend/src/pages/services-list/ServicesListPage.test.tsx
git commit -m "feat: restyle shell and services list as signal console"
```

### Task 3: Restructure service detail into the Signal Console layout

**Files:**
- Modify: `frontend/src/pages/service-detail/ServiceDetailPage.tsx`
- Create: `frontend/src/pages/service-detail/ServiceDetailPage.test.tsx`

- [ ] **Step 1: Write the failing overview hierarchy test**

```tsx
it("shows the service signal hero before the overview panels", async () => {
  mockUseServiceDashboardQuery.mockReturnValue({
    data: buildDashboard(),
    dataUpdatedAt: Date.now(),
    isPending: false,
    error: null,
  });

  renderPage();

  expect(await screen.findByText("billing-sync")).toBeInTheDocument();
  expect(screen.getByText(/runtime signal/i)).toBeInTheDocument();
  expect(screen.getByRole("link", { name: /overview/i })).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the new test to confirm it fails**

Run: `pnpm --dir frontend vitest run src/pages/service-detail/ServiceDetailPage.test.tsx`
Expected: FAIL because the new hierarchy markers are not present

- [ ] **Step 3: Rework the top-level detail layout and overview section**

```tsx
<div className="ref-detail-content">
  <section className="signal-console-detail-hero">
    <div className="signal-console-detail-headline">
      <span className="signal-console-kicker">{isZh ? "服务信号" : "Service signal"}</span>
      <div className="ref-detail-title-row">
        <h2>{serviceName}</h2>
        <StatusBadge {...getServiceStatusBadge(serviceStatus, isZh)} />
      </div>
    </div>
    <div className="signal-console-detail-metric">
      <span className="signal-console-kicker">{isZh ? "运行信号" : "Runtime signal"}</span>
      <strong>{runtimeSignals[0]?.value ?? "—"}</strong>
    </div>
  </section>

  {currentView === "overview" ? (
    <>
      <section className="ref-summary-band signal-console-summary-band">
        <BandStat label={isZh ? "异常任务" : "Failing tasks"} value={String(dashboard.failing_task_count)} />
        <BandStat label={isZh ? "拓扑哈希" : "Topology hashes"} value={String(dashboard.topology_hashes.length)} />
        <BandStat label={isZh ? "命令总数" : "Commands"} value={String(dashboard.command_overview.statuses.total)} />
        <BandStat label={isZh ? "能力数" : "Capabilities"} value={String(summarizeCapabilities(sessions).length)} />
      </section>
      <section className="ref-overview-grid ref-overview-grid-compact">
        <Panel className="ref-card-panel ref-card-panel-compact" title={isZh ? "基础信息" : "Basic information"}>
          <div className="ref-info-grid ref-info-grid-compact">
            <InfoPair label={isZh ? "时间" : "Timeline"} value={<strong>{formatDateTime(dashboard.service.created_at)}</strong>} />
            <InfoPair label={isZh ? "部署" : "Deployment"} value={<strong>{dashboard.service.latest_deployment_version}</strong>} />
            <InfoPair label={isZh ? "拓扑" : "Topology"} value={<strong>{formatIdentifierPreview(dashboard.service.latest_topology_hash)}</strong>} />
            <InfoPair label={isZh ? "可见性" : "Visibility"} value={<strong>{formatRelativeTime(dashboard.service.last_seen_at)}</strong>} />
          </div>
        </Panel>
        <Panel className="ref-card-panel ref-card-panel-compact" title={isZh ? "运行摘要" : "Runtime signals"}>
          <div className="ref-compact-kpi-grid">{runtimeSignals.map((item) => <CompactKpi key={item.label} label={item.label} value={item.value} />)}</div>
        </Panel>
      </section>
    </>
  ) : null}
</div>
```

- [ ] **Step 4: Keep instances, tasks, and commands table-first while restyling the rail and section headers**

```tsx
<aside className="ref-side-nav signal-console-rail">
  <Link className={currentView === "overview" ? "ref-side-nav-item is-active" : "ref-side-nav-item"} to={buildViewHref("overview")}>
    <span>{isZh ? "概览" : "Overview"}</span>
  </Link>
</aside>
```

- [ ] **Step 5: Run the service detail test**

Run: `pnpm --dir frontend vitest run src/pages/service-detail/ServiceDetailPage.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/service-detail/ServiceDetailPage.tsx frontend/src/pages/service-detail/ServiceDetailPage.test.tsx
git commit -m "feat: restyle service detail as signal console"
```

### Task 4: Full verification and cleanup

**Files:**
- Modify: `docs/superpowers/specs/2026-05-16-control-plane-nothing-signal-console-design.md` (only if scope or validation notes need correction)

- [ ] **Step 1: Run the focused frontend suite**

Run: `pnpm --dir frontend vitest run src/components/layout/AppShell.test.tsx src/pages/services-list/ServicesListPage.test.tsx src/pages/service-detail/ServiceDetailPage.test.tsx`
Expected: PASS

- [ ] **Step 2: Run a production build for layout regressions**

Run: `pnpm --dir frontend build`
Expected: build completes successfully

- [ ] **Step 3: Review the diff against the spec**

```bash
git diff -- frontend/src/components/layout/AppShell.tsx frontend/src/pages/services-list/ServicesListPage.tsx frontend/src/pages/service-detail/ServiceDetailPage.tsx frontend/src/styles.css frontend/src/styles/nothing-signal-console.css
```

Expected: only the approved shell, services list, service detail, and scoped style-layer changes appear

- [ ] **Step 4: Commit the verification pass**

```bash
git add frontend/src/components/layout/AppShell.tsx frontend/src/pages/services-list/ServicesListPage.tsx frontend/src/pages/service-detail/ServiceDetailPage.tsx frontend/src/styles.css frontend/src/styles/nothing-signal-console.css frontend/src/pages/services-list/ServicesListPage.test.tsx frontend/src/pages/service-detail/ServiceDetailPage.test.tsx
git commit -m "test: verify signal console redesign"
```

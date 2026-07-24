# Service Detail Page Header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework only the service detail page header into the selected Vibehub Page Header reference shape.

**Architecture:** Keep all service-detail queries, commands, dialogs, tabs, and routes unchanged. Replace the local header JSX with a stage/card layout and add one scoped foundation stylesheet that only targets `.signal-console-service-detail`.

**Tech Stack:** React, TypeScript, React Router, existing Control Plane CSS imports, Vitest, Vite.

---

## File Structure

- Create: `frontend/src/styles/foundation/service-detail-header.css`
  - Holds the new local Vibehub Page Header styling for the service detail page.
- Modify: `frontend/src/styles/foundation/index.css`
  - Imports the new stylesheet without touching dirty global style files.
- Modify: `frontend/src/pages/service-detail/ServiceDetailPage.tsx`
  - Replaces the header markup while preserving current data and actions.
- Test: `frontend/src/pages/service-detail/ServiceDetailPage.test.tsx`
  - Existing smoke test should continue to pass; update only if semantic text changes require it.

### Task 1: Commit This Plan

- [ ] **Step 1: Stage the plan**

```bash
git add docs/superpowers/plans/2026-06-19-service-detail-page-header.md
```

- [ ] **Step 2: Commit the plan**

```bash
git commit -m "docs: plan service detail header refactor"
```

Expected: one docs-only commit.

### Task 2: Refactor the Header Markup

- [ ] **Step 1: Replace the current topbar structure**

In `frontend/src/pages/service-detail/ServiceDetailPage.tsx`, replace the current `<section className="ref-detail-topbar">...</section>` with:

```tsx
<section className="service-detail-header-stage" aria-labelledby="service-detail-title">
  <div className="service-detail-header-card">
    <div className="service-detail-header-main">
      <div className="service-detail-heading">
        <Link className="ref-back-button service-detail-back-button" to={`/services?environment=${environment}`}>
          <span aria-hidden="true" className="ref-back-button-icon service-detail-back-icon">
            ‹
          </span>
          <span className="ref-back-button-label">{isZh ? "返回" : "Back"}</span>
        </Link>
        <div className="service-detail-title-copy">
          <span className="signal-console-kicker">
            {t("serviceDetail.eyebrow", { environment: t(`environment.${environment}`) })}
          </span>
          <div className="service-detail-title-line">
            <h2 id="service-detail-title">{serviceName}</h2>
            <StatusBadge {...getServiceStatusBadge(serviceStatus, isZh)} />
          </div>
        </div>
      </div>
      <div className="service-detail-header-actions">
        <!-- move the existing segmented control, sync menu, and restart button here unchanged -->
      </div>
    </div>
    <div className="service-detail-header-meta">
      <p className="service-detail-meta-line">...</p>
      <div className="service-detail-runtime-chip">...</div>
    </div>
  </div>
</section>
```

- [ ] **Step 2: Preserve existing controls**

Move the existing `SegmentedControl`, sync menu, and restart button into `.service-detail-header-actions` without changing handlers, disabled states, permission gates, or labels.

- [ ] **Step 3: Move runtime signal into metadata**

Move `primaryRuntimeSignal` into `.service-detail-runtime-chip` and keep its `title={primaryRuntimeSignal?.note ?? undefined}` behavior.

### Task 3: Add Scoped Styling

- [ ] **Step 1: Add `service-detail-header.css`**

Create `frontend/src/styles/foundation/service-detail-header.css` with styles for:

- `service-detail-header-stage`: warm gray preview band.
- `service-detail-header-card`: centered white 8px-radius card.
- `service-detail-header-main`: title/actions row.
- `service-detail-heading`: back affordance plus title.
- `service-detail-header-meta`: muted metadata line and runtime chip.
- responsive stacking below 760px.

- [ ] **Step 2: Import the stylesheet**

Add this line to `frontend/src/styles/foundation/index.css` after `command-center.css`:

```css
@import "./service-detail-header.css";
```

### Task 4: Verify

- [ ] **Step 1: Run the focused test**

```bash
cd frontend && pnpm test -- src/pages/service-detail/ServiceDetailPage.test.tsx
```

Expected: service detail test passes.

- [ ] **Step 2: Run the frontend build**

```bash
cd frontend && pnpm build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 3: Commit implementation**

```bash
git add frontend/src/pages/service-detail/ServiceDetailPage.tsx frontend/src/styles/foundation/index.css frontend/src/styles/foundation/service-detail-header.css
git commit -m "style: refactor service detail header"
```

Expected: implementation commit includes only the service detail header files.

## Self-Review

- Spec coverage: The selected local service detail header refactor is covered by Tasks 2 and 3.
- Placeholder scan: No implementation placeholders remain for the actual files; the abbreviated JSX in Task 2 is a shape reference because the existing controls are preserved in place.
- Type consistency: No new exported types or APIs are introduced.

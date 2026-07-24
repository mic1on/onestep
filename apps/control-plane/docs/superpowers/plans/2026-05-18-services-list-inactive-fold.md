# Services List Inactive Fold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move services whose `last_seen_at` is older than the configured inactivity threshold into a collapsed section at the bottom of the services list.

**Architecture:** Keep the API response shape unchanged and implement the behavior entirely in the frontend. Read `VITE_SERVICE_INACTIVE_DAYS` through a small runtime-config helper, partition the already-filtered services list into active and inactive groups, and render the inactive group inside a collapsed `<details>` block that reuses the existing row layout.

**Tech Stack:** React, TypeScript, Vite env config, i18next, Vitest, Testing Library

---

### Task 1: Add inactivity threshold config parsing

**Files:**
- Modify: `frontend/src/lib/runtime-config.ts`
- Create: `frontend/src/lib/runtime-config.test.ts`
- Modify: `frontend/.env.example`

- [ ] **Step 1: Write the failing runtime-config tests**

```ts
import { afterEach, describe, expect, it, vi } from "vitest";

import { getInactiveServiceDays } from "./runtime-config";

describe("getInactiveServiceDays", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("returns the configured positive env value", () => {
    vi.stubEnv("VITE_SERVICE_INACTIVE_DAYS", "5");

    expect(getInactiveServiceDays()).toBe(5);
  });

  it("falls back to 3 when the env value is invalid", () => {
    vi.stubEnv("VITE_SERVICE_INACTIVE_DAYS", "abc");

    expect(getInactiveServiceDays()).toBe(3);
  });

  it("falls back to 3 when the env value is empty", () => {
    vi.stubEnv("VITE_SERVICE_INACTIVE_DAYS", "");

    expect(getInactiveServiceDays()).toBe(3);
  });
});
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `pnpm --dir frontend vitest run src/lib/runtime-config.test.ts`
Expected: FAIL because `getInactiveServiceDays` does not exist yet

- [ ] **Step 3: Add the runtime-config helper**

```ts
type RuntimeConfig = {
  apiBaseUrl?: string;
};

const DEFAULT_INACTIVE_SERVICE_DAYS = 3;

function parsePositiveNumber(value: string | undefined) {
  if (!value) {
    return undefined;
  }

  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return undefined;
  }

  return parsed;
}

export function getInactiveServiceDays() {
  return parsePositiveNumber(import.meta.env.VITE_SERVICE_INACTIVE_DAYS) ?? DEFAULT_INACTIVE_SERVICE_DAYS;
}
```

- [ ] **Step 4: Document the env in the example file**

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
VITE_SERVICE_INACTIVE_DAYS=3
```

- [ ] **Step 5: Run the runtime-config test to verify it passes**

Run: `pnpm --dir frontend vitest run src/lib/runtime-config.test.ts`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/lib/runtime-config.ts frontend/src/lib/runtime-config.test.ts frontend/.env.example
git commit -m "feat: add services inactivity threshold config"
```

### Task 2: Group inactive services into a collapsed section

**Files:**
- Modify: `frontend/src/pages/services-list/ServicesListPage.tsx`
- Modify: `frontend/src/pages/services-list/ServicesListPage.test.tsx`
- Modify: `frontend/src/lib/i18n.ts`

- [ ] **Step 1: Extend the services list test with inactive-section expectations**

```tsx
import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const now = new Date("2026-05-20T08:00:00Z");
const stale = new Date("2026-05-15T08:00:00Z").toISOString();

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(now);
  vi.stubEnv("VITE_SERVICE_INACTIVE_DAYS", "3");
});

afterEach(() => {
  mockUseServicesQuery.mockReset();
  vi.unstubAllEnvs();
  vi.useRealTimers();
});

it("moves long inactive services into the collapsed section", async () => {
  mockUseServicesQuery.mockReturnValue({
    data: buildServicesResponse(),
    isPending: false,
    error: null,
  });

  renderPage();

  expect(await screen.findByText("billing-sync")).toBeInTheDocument();
  expect(screen.getByText("Long inactive services (1)")).toBeInTheDocument();

  const activeTable = document.querySelector(".ref-table-card .ref-table-body");
  expect(activeTable).not.toBeNull();
  expect(within(activeTable as HTMLElement).getByText("billing-sync")).toBeInTheDocument();
  expect(within(activeTable as HTMLElement).queryByText("audit-relay")).not.toBeInTheDocument();

  const inactiveDetails = screen.getByText("Long inactive services (1)").closest("details");
  expect(inactiveDetails).not.toBeNull();
  expect(inactiveDetails).not.toHaveAttribute("open");
  expect(within(inactiveDetails as HTMLElement).getByText("audit-relay")).toBeInTheDocument();
  expect(within(inactiveDetails as HTMLElement).getByText("No activity in the last 3 days.")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run the services list test to verify it fails**

Run: `pnpm --dir frontend vitest run src/pages/services-list/ServicesListPage.test.tsx`
Expected: FAIL because the inactive section copy and grouping do not exist yet

- [ ] **Step 3: Add the new i18n copy**

```ts
servicesList: {
  eyebrow: "Monitoring surface",
  title: "Service fleet",
  subtitle: "A live catalog of OneStep services, deployment revisions, and topology freshness.",
  visibleServices: "Visible services",
  summaryServices: "Services",
  summaryOnline: "Online",
  summaryReady: "Ready",
  summaryAttention: "Attention",
  inactiveSectionTitle: "Long inactive services ({{count}})",
  inactiveSectionDescription: "No activity in the last {{days}} days.",
}
```

```ts
servicesList: {
  title: "服务列表",
  subtitle: "实时查看 OneStep 服务、发布版本和拓扑新鲜度。",
  visibleServices: "可见服务",
  summaryServices: "服务数",
  summaryOnline: "在线实例",
  summaryReady: "稳定服务",
  summaryAttention: "需关注",
  inactiveSectionTitle: "长期未活跃服务（{{count}}）",
  inactiveSectionDescription: "最近 {{days}} 天无活跃记录。",
}
```

- [ ] **Step 4: Partition the list and render the collapsed inactive group**

```tsx
import { getInactiveServiceDays } from "../../lib/runtime-config";

const DAY_IN_MS = 24 * 60 * 60 * 1000;

export function ServicesListPage() {
  const inactiveServiceDays = getInactiveServiceDays();
  const inactiveThresholdMs = inactiveServiceDays * DAY_IN_MS;

  const query = deferredSearch.trim().toLowerCase();
  const filteredItems = !query
    ? (data?.items ?? [])
    : (data?.items ?? []).filter((service) => service.name.toLowerCase().includes(query));
  const { activeItems, inactiveItems } = partitionServices(filteredItems, inactiveThresholdMs);
  const sortedActiveItems = [...activeItems].sort(compareServicesForSurface);
  const sortedInactiveItems = [...inactiveItems].sort(compareServicesForSurface);
  const sortedItems = [...sortedActiveItems, ...sortedInactiveItems];

  return (
    <div className="ref-console-page signal-console-services-page">
      {sortedActiveItems.length > 0 ? <ServicesTable items={sortedActiveItems} t={t} /> : null}

      {sortedInactiveItems.length > 0 ? (
        <details className="ref-collapse-card">
          <summary>
            <strong>{t("servicesList.inactiveSectionTitle", { count: sortedInactiveItems.length })}</strong>
            <span>{t("servicesList.inactiveSectionDescription", { days: inactiveServiceDays })}</span>
          </summary>
          <div className="ref-collapse-body">
            <ServicesTable items={sortedInactiveItems} t={t} />
          </div>
        </details>
      ) : null}
    </div>
  );
}

function ServicesTable({ items, t }: { items: ServiceSummary[]; t: (key: string, options?: Record<string, unknown>) => string }) {
  return (
    <section className="ref-table-card">
      <div className="ref-table-head">
        <span>{t("servicesList.tableHeaderName")}</span>
        <span>{t("servicesList.tableHeaderCreated")}</span>
        <span>{t("servicesList.tableHeaderLastActive")}</span>
        <span>{t("servicesList.tableHeaderDeployment")}</span>
        <span>{t("servicesList.tableHeaderInstances")}</span>
      </div>

      <div className="ref-table-body">
        {items.map((service) => (
          <ServiceRow key={`${service.environment}:${service.name}`} service={service} t={t} />
        ))}
      </div>
    </section>
  );
}

function partitionServices(services: ServiceSummary[], inactiveThresholdMs: number) {
  return services.reduce(
    (groups, service) => {
      if (isServiceInactive(service, inactiveThresholdMs)) {
        groups.inactiveItems.push(service);
      } else {
        groups.activeItems.push(service);
      }
      return groups;
    },
    { activeItems: [] as ServiceSummary[], inactiveItems: [] as ServiceSummary[] },
  );
}

function isServiceInactive(service: ServiceSummary, inactiveThresholdMs: number) {
  if (service.last_seen_at === null) {
    return true;
  }

  return Date.now() - Date.parse(service.last_seen_at) > inactiveThresholdMs;
}
```

- [ ] **Step 5: Run the services list test to verify it passes**

Run: `pnpm --dir frontend vitest run src/pages/services-list/ServicesListPage.test.tsx`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/services-list/ServicesListPage.tsx frontend/src/pages/services-list/ServicesListPage.test.tsx frontend/src/lib/i18n.ts
git commit -m "feat: fold inactive services in the list"
```

### Task 3: Verify the full change set

**Files:**
- Test: `frontend/src/lib/runtime-config.test.ts`
- Test: `frontend/src/pages/services-list/ServicesListPage.test.tsx`
- Test: `frontend/src/pages/service-detail/ServiceDetailPage.test.tsx`

- [ ] **Step 1: Run the targeted frontend tests together**

Run: `pnpm --dir frontend vitest run src/lib/runtime-config.test.ts src/pages/services-list/ServicesListPage.test.tsx src/pages/service-detail/ServiceDetailPage.test.tsx`
Expected: PASS

- [ ] **Step 2: Run a production build smoke test**

Run: `pnpm --dir frontend build`
Expected: PASS

- [ ] **Step 3: Commit any remaining tracked changes**

```bash
git add frontend/.env.example frontend/src/lib/i18n.ts frontend/src/lib/runtime-config.ts frontend/src/lib/runtime-config.test.ts frontend/src/pages/services-list/ServicesListPage.tsx frontend/src/pages/services-list/ServicesListPage.test.tsx
git commit -m "test: verify inactive service folding flow"
```

import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ServiceListResponse } from "../../lib/api/types";
import { ServicesListPage } from "./ServicesListPage";

const mockUseServicesQuery = vi.fn();

vi.mock("../../features/services/queries", () => ({
  useServicesQuery: (...args: unknown[]) => mockUseServicesQuery(...args),
}));

function buildServicesResponse(): ServiceListResponse {
  const now = new Date("2026-05-20T08:00:00Z").toISOString();
  const stale = new Date("2026-05-15T08:00:00Z").toISOString();
  return {
    items: [
      {
        name: "billing-sync",
        environment: "prod",
        latest_deployment_version: "2026.05.16",
        service_status: "online",
        latest_topology_hash: "hash-a",
        latest_sync_at: now,
        instance_count: 4,
        online_instance_count: 4,
        last_seen_at: now,
        source_kinds: ["manual"],
        task_count: 2,
        created_at: now,
        updated_at: now,
      },
      {
        name: "audit-relay",
        environment: "staging",
        latest_deployment_version: "2026.05.15",
        service_status: "attention",
        latest_topology_hash: null,
        latest_sync_at: null,
        instance_count: 3,
        online_instance_count: 1,
        last_seen_at: stale,
        source_kinds: ["scheduler"],
        task_count: 1,
        created_at: now,
        updated_at: now,
      },
    ],
    total: 2,
    limit: 100,
    offset: 0,
    source_kind_counts: {
      manual: 1,
      scheduler: 1,
    },
    summary: {
      total_services: 2,
      online_services: 1,
      attention_services: 1,
      offline_services: 0,
      ready_services: 1,
      total_instances: 7,
      online_instances: 5,
    },
  };
}

function renderPage() {
  render(
    <MemoryRouter initialEntries={["/services?environment=all"]}>
      <Routes>
        <Route path="/services" element={<ServicesListPage />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ServicesListPage", () => {
  beforeEach(() => {
    vi.spyOn(Date, "now").mockReturnValue(new Date("2026-05-20T08:00:00Z").valueOf());
    vi.stubEnv("VITE_SERVICE_INACTIVE_DAYS", "3");
  });

  afterEach(() => {
    mockUseServicesQuery.mockReset();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  });

  it("shows the signal-console hero and summary band", async () => {
    mockUseServicesQuery.mockReturnValue({
      data: buildServicesResponse(),
      isPending: false,
      error: null,
    });

    renderPage();

    expect(await screen.findByText("Monitoring surface")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Service fleet" })).toBeInTheDocument();
    const heroMetric = screen.getByText("Visible services").closest(".signal-console-metric");
    expect(heroMetric).not.toBeNull();
    expect(within(heroMetric as HTMLElement).getByText("2")).toBeInTheDocument();
    expect(screen.getByText("Attention")).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Search" })).toBeInTheDocument();
    expect(screen.getByText("billing-sync")).toBeInTheDocument();
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

  it("keeps a fully online service green even when its latest sync is stale", async () => {
    const now = new Date("2026-05-20T08:00:00Z").toISOString();
    const staleSync = new Date("2026-05-20T07:30:00Z").toISOString();

    mockUseServicesQuery.mockReturnValue({
      data: {
        items: [
          {
            name: "orders-api",
            environment: "prod",
            latest_deployment_version: "2026.05.20",
            service_status: "online",
            latest_topology_hash: "hash-orders",
            latest_sync_at: staleSync,
            instance_count: 1,
            online_instance_count: 1,
            last_seen_at: now,
            source_kinds: [],
            task_count: 0,
            created_at: now,
            updated_at: now,
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
        source_kind_counts: {},
        summary: {
          total_services: 1,
          online_services: 1,
          attention_services: 0,
          offline_services: 0,
          ready_services: 1,
          total_instances: 1,
          online_instances: 1,
        },
      },
      isPending: false,
      error: null,
    });

    renderPage();

    const row = (await screen.findByText("orders-api")).closest(".ref-table-row");
    expect(row).not.toBeNull();

    const usageFill = row?.querySelector(".ref-usage-fill");
    expect(usageFill).not.toBeNull();
    expect(usageFill).toHaveClass("is-healthy");
    expect(usageFill).not.toHaveClass("is-warning");
  });
});

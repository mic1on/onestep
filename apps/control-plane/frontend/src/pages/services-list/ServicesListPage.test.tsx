import { render, screen, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ServiceListResponse } from "../../lib/api/types";
import { ServicesListPage } from "./ServicesListPage";

const mockUseServicesQuery = vi.fn();

vi.mock("../../features/services/queries", () => ({
  useServicesQuery: (...args: unknown[]) => mockUseServicesQuery(...args),
}));

function buildServicesResponse(): ServiceListResponse {
  const now = new Date("2026-05-16T08:00:00Z").toISOString();
  return {
    items: [
      {
        name: "billing-sync",
        environment: "prod",
        latest_deployment_version: "2026.05.16",
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
        latest_topology_hash: null,
        latest_sync_at: null,
        instance_count: 3,
        online_instance_count: 1,
        last_seen_at: now,
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
  afterEach(() => {
    mockUseServicesQuery.mockReset();
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
});

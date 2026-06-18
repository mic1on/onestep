import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CommandCenterPage } from "./CommandCenterPage";

const mockUseServicesQuery = vi.fn();
const mockUseWorkerAgentsQuery = vi.fn();
const mockUseWorkerDeploymentsQuery = vi.fn();
const mockUseWorkersQuery = vi.fn();
const mockUseConnectorsQuery = vi.fn();
const mockUseCommandStreamStatus = vi.fn();

vi.mock("../../features/services/queries", () => ({
  useServicesQuery: () => mockUseServicesQuery(),
}));

vi.mock("../../features/worker-agents/queries", () => ({
  useWorkerAgentsQuery: () => mockUseWorkerAgentsQuery(),
  useWorkerDeploymentsQuery: () => mockUseWorkerDeploymentsQuery(),
}));

vi.mock("../../features/workers/queries", () => ({
  useWorkersQuery: () => mockUseWorkersQuery(),
}));

vi.mock("../../features/connectors/queries", () => ({
  useConnectorsQuery: () => mockUseConnectorsQuery(),
}));

vi.mock("../../features/commands/useCommandStream", () => ({
  useCommandStreamStatus: () => mockUseCommandStreamStatus(),
}));

const now = "2026-06-18T12:00:00.000Z";

describe("CommandCenterPage", () => {
  afterEach(() => {
    mockUseServicesQuery.mockReset();
    mockUseWorkerAgentsQuery.mockReset();
    mockUseWorkerDeploymentsQuery.mockReset();
    mockUseWorkersQuery.mockReset();
    mockUseConnectorsQuery.mockReset();
    mockUseCommandStreamStatus.mockReset();
  });

  it("renders the command center summary and attention queue", async () => {
    mockUseServicesQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: {
        items: [
          {
            name: "billing-sync",
            environment: "prod",
            latest_deployment_version: "2026.06.18",
            service_status: "attention",
            latest_topology_hash: "hash",
            latest_sync_at: now,
            instance_count: 4,
            online_instance_count: 2,
            last_seen_at: now,
            source_kinds: [],
            task_count: 1,
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
          online_services: 0,
          attention_services: 1,
          offline_services: 0,
          ready_services: 0,
          total_instances: 4,
          online_instances: 2,
        },
      },
    });
    mockUseWorkerAgentsQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: { items: [], total: 0, limit: 100, offset: 0 },
    });
    mockUseWorkerDeploymentsQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: { items: [], total: 0, limit: 100, offset: 0 },
    });
    mockUseWorkersQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: { items: [], total: 0 },
    });
    mockUseConnectorsQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: { items: [], total: 0 },
    });
    mockUseCommandStreamStatus.mockReturnValue({
      phase: "connected",
      last_connected_at: Date.now(),
      last_event_at: Date.now(),
      last_error_at: null,
    });

    render(
      <MemoryRouter>
        <CommandCenterPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "What needs attention now" })).toBeInTheDocument();
    expect(screen.getByText("Attention")).toBeInTheDocument();
    expect(screen.getByText("2/4")).toBeInTheDocument();
    expect(screen.getAllByText("billing-sync").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /Open service/i })).toHaveAttribute(
      "href",
      "/services/billing-sync?environment=prod&lookback_minutes=60",
    );
  });
});

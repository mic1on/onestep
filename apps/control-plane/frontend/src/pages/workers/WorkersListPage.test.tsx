import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { WorkersListPage } from "./WorkersListPage";

const mockUseWorkersQuery = vi.fn();

vi.mock("../../features/workers/queries", () => ({
  useWorkersQuery: () => mockUseWorkersQuery(),
  useCreateWorkerMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
}));

function renderPage() {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <WorkersListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

function buildWorker(patch: Partial<{
  id: string;
  name: string;
  description: string;
  handler_package_id: string | null;
  handler_ref: string;
  source_config: { type: string; connector_id: string | null; fields: Record<string, unknown> };
  sink_configs: Array<{ type: string; connector_id: string | null; fields: Record<string, unknown> }>;
  env: Record<string, string>;
  reporting_enabled: boolean;
  reporting_config: { mode: "platform" | "custom"; endpoint_url: string | null };
  reporting_token_configured: boolean;
  status: "ready" | "draft";
  created_at: string;
  updated_at: string;
}> = {}) {
  return {
    id: "11111111-1111-1111-1111-111111111111",
    name: "order-sync",
    description: "sync orders",
    handler_package_id: null,
    handler_ref: "handler:handler",
    source_config: { type: "interval", connector_id: null, fields: {} },
    sink_configs: [],
    env: {},
    reporting_enabled: true,
    reporting_config: { mode: "platform", endpoint_url: null },
    reporting_token_configured: false,
    status: "draft",
    created_at: "2026-06-17T00:00:00Z",
    updated_at: "2026-06-17T00:00:00Z",
    ...patch,
  };
}

describe("WorkersListPage", () => {
  it("renders the step asset workbench and selects the first step by default", () => {
    mockUseWorkersQuery.mockReturnValue({
      data: {
        items: [
          buildWorker({
            id: "11111111-1111-1111-1111-111111111111",
            name: "order-sync",
            description: "sync orders",
            status: "draft",
            sink_configs: [],
          }),
          buildWorker({
            id: "22222222-2222-2222-2222-222222222222",
            name: "invoice-export",
            description: "export invoices",
            status: "ready",
            handler_package_id: "package-1",
            sink_configs: [{ type: "http_sink", connector_id: null, fields: {} }],
          }),
        ],
        total: 2,
      },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "Steps" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Step library" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "order-sync" })).toBeInTheDocument();
    expect(screen.getAllByText("Draft").length).toBeGreaterThan(0);
    expect(screen.getAllByText("0 targets").length).toBeGreaterThan(0);
  });

  it("updates the selected step detail when a step asset is selected", async () => {
    mockUseWorkersQuery.mockReturnValue({
      data: {
        items: [
          buildWorker({
            id: "11111111-1111-1111-1111-111111111111",
            name: "order-sync",
            description: "sync orders",
            status: "draft",
          }),
          buildWorker({
            id: "22222222-2222-2222-2222-222222222222",
            name: "invoice-export",
            description: "export invoices",
            status: "ready",
            handler_package_id: "package-1",
            sink_configs: [{ type: "http_sink", connector_id: null, fields: {} }],
          }),
        ],
        total: 2,
      },
      isPending: false,
      error: null,
    });

    renderPage();

    fireEvent.click(screen.getByRole("button", { name: /invoice-export/i }));

    expect(screen.getByRole("heading", { name: "invoice-export" })).toBeInTheDocument();
    expect(screen.getAllByText("Ready").length).toBeGreaterThan(0);
    expect(screen.getAllByText("1 target").length).toBeGreaterThan(0);
  });

  it("renders empty state when no steps", () => {
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [], total: 0 },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByText("No steps yet")).toBeInTheDocument();
  });
});

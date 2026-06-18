import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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

describe("WorkersListPage", () => {
  it("renders the header and worker rows", () => {
    mockUseWorkersQuery.mockReturnValue({
      data: {
        items: [
          {
            id: "11111111-1111-1111-1111-111111111111",
            name: "order-sync",
            description: "sync orders",
            handler_package_id: null,
            handler_ref: "handler:handler",
            source_config: { type: "interval", connector_id: null, fields: {} },
            sink_configs: [],
            status: "draft",
            created_at: "2026-06-17T00:00:00Z",
            updated_at: "2026-06-17T00:00:00Z",
          },
        ],
        total: 1,
      },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "Workers" })).toBeInTheDocument();
    expect(screen.getByText("order-sync")).toBeInTheDocument();
  });

  it("renders empty state when no workers", () => {
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [], total: 0 },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByText("No workers yet")).toBeInTheDocument();
  });
});

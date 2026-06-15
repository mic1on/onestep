import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { PipelinesListPage } from "./PipelinesListPage";

const mockUsePipelinesQuery = vi.fn();

vi.mock("../../features/pipelines/queries", () => ({
  useCreatePipelineMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
  useDeletePipelineMutation: () => ({ isPending: false, mutateAsync: vi.fn() }),
  usePipelinesQuery: (...args: unknown[]) => mockUsePipelinesQuery(...args),
}));

function renderPage() {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <PipelinesListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("PipelinesListPage", () => {
  it("renders pipeline rows and create action", () => {
    mockUsePipelinesQuery.mockReturnValue({
      data: {
        items: [
          {
            id: "11111111-1111-1111-1111-111111111111",
            name: "Daily Sync",
            description: "Build from UI",
            graph: { nodes: [], edges: [] },
            status: "valid",
            created_at: "2026-06-15T00:00:00Z",
            updated_at: "2026-06-15T00:00:00Z",
          },
        ],
      },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "Pipelines" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create pipeline" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Daily Sync/ })).toBeInTheDocument();
    expect(screen.getByText("valid")).toBeInTheDocument();
  });
});

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkerAgentsPage } from "./WorkerAgentsPage";

const mockUseWorkerAgentsQuery = vi.fn();

vi.mock("../../features/worker-agents/queries", () => ({
  useWorkerAgentsQuery: () => mockUseWorkerAgentsQuery(),
}));

function renderPage() {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <WorkerAgentsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WorkerAgentsPage", () => {
  beforeEach(() => {
    mockUseWorkerAgentsQuery.mockReset();
  });

  it("renders agent rows", () => {
    mockUseWorkerAgentsQuery.mockReturnValue({
      data: {
        items: [
          {
            worker_agent_id: "11111111-1111-1111-1111-111111111111",
            display_name: "prod-runner-1",
            status: "online",
            execution_mode: "subprocess",
            max_concurrent_deployments: 4,
            used_slots: 2,
            labels: { env: "prod" },
            capabilities: [],
            agent_version: "1.0.0",
            onestep_version: "1.2.0",
            python_version: "3.12",
            platform: {},
            registered_at: "2026-06-15T00:00:00Z",
            last_seen_at: "2026-06-16T00:00:00Z",
            created_at: "2026-06-15T00:00:00Z",
            updated_at: "2026-06-16T00:00:00Z",
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
      },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByRole("heading", { name: "Agents" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /prod-runner-1/ })).toBeInTheDocument();
    expect(screen.getByText("online")).toBeInTheDocument();
  });

  it("renders empty state when no agents", () => {
    mockUseWorkerAgentsQuery.mockReturnValue({
      data: { items: [], total: 0, limit: 100, offset: 0 },
      isPending: false,
      error: null,
    });

    renderPage();

    expect(screen.getByText("No agents registered")).toBeInTheDocument();
  });

  it("opens the add agent guide and generates an install command", async () => {
    const user = userEvent.setup();
    mockUseWorkerAgentsQuery.mockReturnValue({
      data: { items: [], total: 0, limit: 100, offset: 0 },
      isPending: false,
      error: null,
    });

    renderPage();

    await user.click(screen.getByRole("button", { name: /Add agent/ }));
    await user.clear(screen.getByLabelText("Registration token"));
    await user.type(screen.getByLabelText("Registration token"), "registration-token");
    await user.clear(screen.getByLabelText("Agent name"));
    await user.type(screen.getByLabelText("Agent name"), "prod-runner-2");

    expect(screen.getByRole("dialog", { name: "Add worker agent" })).toBeInTheDocument();
    expect(screen.getByText(/curl -fsSL http:\/\/localhost:3000\/agent-install\.sh/)).toBeInTheDocument();
    expect(screen.getByText(/\| bash -s --/)).toBeInTheDocument();
    expect(screen.getByText(/--token 'registration-token'/)).toBeInTheDocument();
    expect(screen.getByText(/--name 'prod-runner-2'/)).toBeInTheDocument();
    expect(screen.getByText(/--max-concurrency 1/)).toBeInTheDocument();
  });
});

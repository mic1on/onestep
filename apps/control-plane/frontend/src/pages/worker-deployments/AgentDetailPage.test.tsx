import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

import { AgentDetailPage } from "./AgentDetailPage";

const mockUseWorkerAgentQuery = vi.fn();
const mockUseWorkerDeploymentsQuery = vi.fn();
const mockStart = vi.fn();
const mockStop = vi.fn();
const mockRestart = vi.fn();

vi.mock("../../features/worker-agents/queries", () => ({
  useWorkerAgentQuery: (...args: unknown[]) => mockUseWorkerAgentQuery(...args),
  useWorkerDeploymentsQuery: (...args: unknown[]) => mockUseWorkerDeploymentsQuery(...args),
  useStartWorkerDeploymentMutation: () => ({ isPending: false, mutateAsync: mockStart }),
  useStopWorkerDeploymentMutation: () => ({ isPending: false, mutateAsync: mockStop }),
  useRestartWorkerDeploymentMutation: () => ({ isPending: false, mutateAsync: mockRestart }),
}));

vi.mock("../../features/worker-agents/components/DeployDialog", () => ({
  DeployDialog: () => null,
}));

const AGENT_ID = "22222222-2222-2222-2222-222222222222";

function renderPage() {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/agents/${AGENT_ID}`]}>
        <Routes>
          <Route path="/agents/:agentId" element={<AgentDetailPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("AgentDetailPage", () => {
  it("renders agent header and deployment tab with actions", async () => {
    const user = userEvent.setup();
    mockUseWorkerAgentQuery.mockReturnValue({
      data: {
        worker_agent_id: AGENT_ID,
        display_name: "prod-runner-1",
        status: "online",
        execution_mode: "subprocess",
        max_concurrent_deployments: 4,
        used_slots: 1,
        labels: {},
        capabilities: [],
        agent_version: "1.0.0",
        onestep_version: "1.2.0",
        python_version: null,
        platform: {},
        registered_at: "2026-06-15T00:00:00Z",
        last_seen_at: "2026-06-16T00:00:00Z",
        created_at: "2026-06-15T00:00:00Z",
        updated_at: "2026-06-16T00:00:00Z",
      },
      isPending: false,
      error: null,
    });
    mockUseWorkerDeploymentsQuery.mockReturnValue({
      data: {
        items: [
          {
            deployment_id: "33333333-3333-3333-3333-333333333333",
            workflow_package_id: "44444444-4444-4444-4444-444444444444",
            worker_agent_id: AGENT_ID,
            desired_status: "running",
            observed_status: "running",
            runtime_instance_id: null,
            execution_mode: "subprocess",
            params: {},
            env: {},
            credential_refs: [],
            package_checksum: "abc123",
            last_error_code: null,
            last_error_message: null,
            assigned_at: "2026-06-16T00:00:00Z",
            started_at: "2026-06-16T00:00:01Z",
            finished_at: null,
            created_by: "system",
            created_at: "2026-06-16T00:00:00Z",
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

    expect(screen.getByRole("heading", { name: "prod-runner-1" })).toBeInTheDocument();
    expect(screen.getByRole("tablist", { name: "Agent detail sections" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Overview" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Deployments" }));

    expect(screen.getByRole("button", { name: "Stop" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Restart" })).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "Deploy workflow" })).toHaveLength(2);
  });
});

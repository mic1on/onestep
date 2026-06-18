import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { WorkerEditorPage } from "./WorkerEditorPage";

const mockCreateWorker = vi.fn();
const mockUpdateWorker = vi.fn();
const mockDeployWorker = vi.fn();
const mockUploadPackage = vi.fn();
const mockUseConnectorsQuery = vi.fn();
const mockUseWorkersQuery = vi.fn();

vi.mock("@monaco-editor/react", () => ({
  default: ({ value, onChange }: { value?: string; onChange?: (value?: string) => void }) => (
    <textarea
      aria-label="Handler code"
      value={value ?? ""}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}));

vi.mock("../../features/workers/queries", () => ({
  useWorkersQuery: () => mockUseWorkersQuery(),
  useCreateWorkerMutation: () => ({ isPending: false, mutateAsync: mockCreateWorker }),
  useUpdateWorkerMutation: () => ({ isPending: false, mutateAsync: mockUpdateWorker }),
  useDeployWorkerMutation: () => ({ isPending: false, mutateAsync: mockDeployWorker }),
}));

vi.mock("../../features/connectors/queries", () => ({
  useConnectorsQuery: () => mockUseConnectorsQuery(),
}));

vi.mock("../../features/worker-agents/queries", () => ({
  useCreateWorkflowPackageMutation: () => ({
    isPending: false,
    mutateAsync: mockUploadPackage,
  }),
}));

const EXISTING_WORKER = {
  id: "074b17d8-ed91-40ec-9d2b-5b8329bbea01",
  name: "order-sync",
  description: "sync orders",
  handler_package_id: null,
  handler_ref: "handler:handler",
  source_config: { type: "interval", connector_id: null, fields: {} },
  sink_configs: [],
  env: {},
  status: "draft",
  created_at: "2026-06-17T00:00:00Z",
  updated_at: "2026-06-17T00:00:00Z",
};

function renderPage(route = "/workers/new") {
  const queryClient = new QueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/workers/:workerId" element={<WorkerEditorPage />} />
          <Route path="/agents/:agentId" element={<div />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("WorkerEditorPage", () => {
  beforeEach(() => {
    mockCreateWorker.mockReset();
    mockUpdateWorker.mockReset();
    mockDeployWorker.mockReset();
    mockUploadPackage.mockReset();
    mockUseConnectorsQuery.mockReturnValue({
      data: { items: [] },
      isPending: false,
      error: null,
    });
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [] },
      isPending: false,
      error: null,
    });
  });

  it("renders the build panel for a new worker", () => {
    renderPage();

    expect(screen.getByText("Worker setup")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Code source" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Configuration" })).toBeInTheDocument();
  });

  it("renders the config sidebar sections when editing", () => {
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [EXISTING_WORKER] },
      isPending: false,
      error: null,
    });
    renderPage(`/workers/${EXISTING_WORKER.id}`);

    expect(screen.getByRole("button", { name: "General" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Handler ref" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Trigger" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Targets" })).toBeInTheDocument();
  });

  it("auto-saves config changes (debounced) when editing", async () => {
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [EXISTING_WORKER] },
      isPending: false,
      error: null,
    });
    mockUpdateWorker.mockResolvedValue({});
    renderPage(`/workers/${EXISTING_WORKER.id}`);

    // Navigate to general config and change the name.
    fireEvent.click(screen.getByRole("button", { name: "General" }));
    const nameInput = screen.getByLabelText("Name");
    fireEvent.change(nameInput, { target: { value: "renamed-worker" } });

    // Auto-save fires after the 600ms debounce.
    await waitFor(
      () => {
        expect(mockUpdateWorker).toHaveBeenCalledWith(
          expect.objectContaining({ name: "renamed-worker" }),
        );
      },
      { timeout: 2000 },
    );
  });

  it("edits environment variables and auto-saves normalized env", async () => {
    const user = userEvent.setup();
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [EXISTING_WORKER] },
      isPending: false,
      error: null,
    });
    mockUpdateWorker.mockResolvedValue({});
    renderPage(`/workers/${EXISTING_WORKER.id}`);

    await user.click(screen.getByRole("button", { name: "Environment variables" }));
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(screen.getByLabelText("Key 1"), "API_TOKEN");
    await user.type(screen.getByLabelText("Value 1"), "secret-token");

    await waitFor(
      () => {
        expect(mockUpdateWorker).toHaveBeenCalledWith(
          expect.objectContaining({ env: { API_TOKEN: "secret-token" } }),
        );
      },
      { timeout: 2000 },
    );
  });

  it("saves the current configuration when the save button is clicked", async () => {
    const user = userEvent.setup();
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [EXISTING_WORKER] },
      isPending: false,
      error: null,
    });
    mockUpdateWorker.mockResolvedValue({});
    renderPage(`/workers/${EXISTING_WORKER.id}`);

    await user.click(screen.getByRole("button", { name: "Environment variables" }));
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(screen.getByLabelText("Key 1"), " API_TOKEN ");
    await user.type(screen.getByLabelText("Value 1"), "secret-token");
    mockUpdateWorker.mockClear();

    await user.click(screen.getByRole("button", { name: "Save configuration" }));

    expect(mockUpdateWorker).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "order-sync",
        handler_ref: "handler:handler",
        env: { API_TOKEN: "secret-token" },
      }),
    );
  });

  it("sends current environment variables when deploying", async () => {
    const user = userEvent.setup();
    const promptSpy = vi.spyOn(window, "prompt").mockReturnValue("agent-1");
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [EXISTING_WORKER] },
      isPending: false,
      error: null,
    });
    mockDeployWorker.mockResolvedValue({ deployment_id: "deployment-1" });
    renderPage(`/workers/${EXISTING_WORKER.id}`);

    await user.click(screen.getByRole("button", { name: "Environment variables" }));
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(screen.getByLabelText("Key 1"), "API_TOKEN");
    await user.type(screen.getByLabelText("Value 1"), "secret-token");
    await user.click(screen.getAllByRole("button", { name: "Deploy to agent" })[0]);

    expect(mockDeployWorker).toHaveBeenCalledWith({
      worker_agent_id: "agent-1",
      env: { API_TOKEN: "secret-token" },
    });
    promptSpy.mockRestore();
  });

  it("packages code and binds the package when editing", async () => {
    const user = userEvent.setup();
    mockUseWorkersQuery.mockReturnValue({
      data: { items: [EXISTING_WORKER] },
      isPending: false,
      error: null,
    });
    mockUploadPackage.mockResolvedValue({
      package_id: "99999999-9999-9999-9999-999999999999",
    });
    mockUpdateWorker.mockResolvedValue({});

    renderPage(`/workers/${EXISTING_WORKER.id}`);

    await user.click(screen.getByRole("button", { name: "Package code" }));

    await waitFor(() => {
      expect(mockUploadPackage).toHaveBeenCalledWith(
        expect.objectContaining({
          entrypoint: "handler.py",
        }),
      );
    });
  });
});

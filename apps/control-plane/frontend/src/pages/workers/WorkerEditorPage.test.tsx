import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { WorkerAgentSummary, WorkerSummary } from "../../lib/api/types";
import { WorkerEditorPage } from "./WorkerEditorPage";

const mockCreateWorker = vi.fn();
const mockUpdateWorker = vi.fn();
const mockDeployWorker = vi.fn();
const mockDownloadWorkerPackage = vi.fn();
const mockUploadPackage = vi.fn();
const mockUseConnectorsQuery = vi.fn();
const mockUseWorkerQuery = vi.fn();
const mockUseWorkerAgentsQuery = vi.fn();

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
  useWorkerQuery: (workerId: string | undefined, enabled: boolean) =>
    mockUseWorkerQuery(workerId, enabled),
  useCreateWorkerMutation: () => ({ isPending: false, mutateAsync: mockCreateWorker }),
  useUpdateWorkerMutation: () => ({ isPending: false, mutateAsync: mockUpdateWorker }),
  useDeployWorkerMutation: () => ({ isPending: false, mutateAsync: mockDeployWorker }),
  useDownloadWorkerPackageMutation: () => ({
    isPending: false,
    mutateAsync: mockDownloadWorkerPackage,
  }),
}));

vi.mock("../../features/connectors/queries", () => ({
  useConnectorsQuery: () => mockUseConnectorsQuery(),
}));

vi.mock("../../features/worker-agents/queries", () => ({
  useWorkerAgentsQuery: () => mockUseWorkerAgentsQuery(),
  useCreateWorkflowPackageMutation: () => ({
    isPending: false,
    mutateAsync: mockUploadPackage,
  }),
}));

const EXISTING_WORKER: WorkerSummary = {
  id: "074b17d8-ed91-40ec-9d2b-5b8329bbea01",
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
};

function buildAgent(patch: Partial<WorkerAgentSummary>): WorkerAgentSummary {
  return {
    worker_agent_id: "agent-1",
    display_name: "prod-runner-1",
    status: "online",
    execution_mode: "subprocess",
    max_concurrent_deployments: 4,
    used_slots: 1,
    labels: {},
    capabilities: [],
    agent_version: "1.0.0",
    onestep_version: "1.2.0",
    python_version: "3.12",
    platform: {},
    registered_at: "2026-06-15T00:00:00Z",
    last_seen_at: "2026-06-16T00:00:00Z",
    created_at: "2026-06-15T00:00:00Z",
    updated_at: "2026-06-16T00:00:00Z",
    ...patch,
  };
}

function renderPage(route = "/workers/new") {
  const queryClient = new QueryClient();
  const renderUi = () => (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <Routes>
          <Route path="/workers/:workerId" element={<WorkerEditorPage />} />
          <Route path="/agents/:agentId/deployments/:deploymentId/events" element={<div>Deployment events</div>} />
          <Route path="/agents/:agentId" element={<div />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  const view = render(renderUi());
  return { ...view, rerenderPage: () => view.rerender(renderUi()) };
}

function openConfigurationTab() {
  fireEvent.click(screen.getByRole("button", { name: "Configuration" }));
}

function openCodeTab() {
  fireEvent.click(screen.getByRole("button", { name: "Code source" }));
}

describe("WorkerEditorPage", () => {
  beforeEach(() => {
    mockCreateWorker.mockReset();
    mockUpdateWorker.mockReset();
    mockDeployWorker.mockReset();
    mockDownloadWorkerPackage.mockReset();
    mockUploadPackage.mockReset();
    mockUseConnectorsQuery.mockReturnValue({
      data: { items: [] },
      isPending: false,
      error: null,
    });
    mockUseWorkerQuery.mockReturnValue({
      data: undefined,
      isPending: false,
      error: null,
    });
    mockUseWorkerAgentsQuery.mockReturnValue({
      data: { items: [], total: 0, limit: 100, offset: 0 },
      isPending: false,
      error: null,
    });
  });

  it("renders the editor tabs for a new step", () => {
    renderPage();

    expect(screen.getByRole("button", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Configuration" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Code source" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deploy" })).toBeInTheDocument();
  });

  it("uses the top tabs without an extra nested studio rail", () => {
    renderPage();

    expect(screen.queryByLabelText("Step Studio navigation")).not.toBeInTheDocument();
  });

  it("generates a handler that uses the runtime payload directly", () => {
    renderPage();
    openCodeTab();

    const handlerCode = screen.getByLabelText("Handler code") as HTMLTextAreaElement;
    expect(handlerCode.value).toContain("payload = item");
    expect(handlerCode.value).not.toContain("item.payload");
  });

  it("renders the config sidebar sections when editing", () => {
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    renderPage(`/workers/${EXISTING_WORKER.id}`);
    openConfigurationTab();

    expect(screen.getByRole("button", { name: "General" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Handler ref" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Trigger" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Targets" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Data reporting" })).toBeInTheDocument();
  });

  it("configures interval triggers with operational labels while preserving fields", async () => {
    const user = userEvent.setup();
    mockUseWorkerQuery.mockReturnValue({
      data: {
        ...EXISTING_WORKER,
        source_config: {
          type: "interval",
          connector_id: null,
          fields: { minutes: 15, immediate: false },
        },
      },
      isPending: false,
      error: null,
    });
    mockUpdateWorker.mockResolvedValue({});
    renderPage(`/workers/${EXISTING_WORKER.id}`);

    await user.click(screen.getByRole("button", { name: "Configuration" }));
    await user.click(screen.getByRole("button", { name: "Trigger" }));

    expect(screen.getByText("Run every")).toBeInTheDocument();
    expect(screen.getByText("15 min")).toBeInTheDocument();
    expect(screen.getByText("Wait for first interval")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Configure" }));
    const intervalInput = screen.getByRole("spinbutton", { name: /Run every/ });
    await user.clear(intervalInput);
    await user.type(intervalInput, "30");
    await user.click(screen.getByRole("combobox", { name: "Unit" }));
    await user.click(screen.getByRole("option", { name: "Seconds" }));
    await user.click(screen.getByRole("button", { name: /Run once right away/ }));
    await user.click(screen.getByRole("button", { name: "Apply trigger" }));
    mockUpdateWorker.mockClear();

    await user.click(screen.getByRole("button", { name: "Save configuration" }));

    expect(mockUpdateWorker).toHaveBeenCalledWith(
      expect.objectContaining({
        source_config: {
          type: "interval",
          connector_id: null,
          fields: {
            seconds: 30,
            immediate: "true",
          },
        },
      }),
    );
  });

  it("hydrates worker details when the edit page data loads after a direct refresh", async () => {
    let workersQueryResult: {
      data: WorkerSummary | undefined;
      isPending: boolean;
      error: unknown;
    } = {
      data: undefined,
      isPending: true,
      error: null,
    };
    mockUseWorkerQuery.mockImplementation(() => workersQueryResult);
    const { rerenderPage } = renderPage(`/workers/${EXISTING_WORKER.id}`);

    workersQueryResult = {
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    };
    rerenderPage();
    openConfigurationTab();

    expect(await screen.findByDisplayValue("order-sync")).toBeInTheDocument();
    expect(screen.getByDisplayValue("sync orders")).toBeInTheDocument();
  });

  it("auto-saves config changes (debounced) when editing", async () => {
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUpdateWorker.mockResolvedValue({});
    renderPage(`/workers/${EXISTING_WORKER.id}`);
    openConfigurationTab();

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
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUpdateWorker.mockResolvedValue({});
    renderPage(`/workers/${EXISTING_WORKER.id}`);
    await user.click(screen.getByRole("button", { name: "Configuration" }));

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

  it("saves custom reporting endpoint and token", async () => {
    const user = userEvent.setup();
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUpdateWorker.mockResolvedValue({});
    renderPage(`/workers/${EXISTING_WORKER.id}`);
    await user.click(screen.getByRole("button", { name: "Configuration" }));

    await user.click(screen.getByRole("button", { name: "Data reporting" }));
    await user.click(screen.getByRole("combobox", { name: "Reporting destination" }));
    await user.click(screen.getByRole("option", { name: "Custom endpoint" }));
    await user.type(screen.getByLabelText("Endpoint URL"), "https://telemetry.example.com");
    await user.type(screen.getByLabelText("Token"), "custom-token");

    await waitFor(
      () => {
        expect(mockUpdateWorker).toHaveBeenCalledWith(
          expect.objectContaining({
            reporting_enabled: true,
            reporting_config: {
              mode: "custom",
              endpoint_url: "https://telemetry.example.com",
            },
            reporting_secret: { token: "custom-token" },
          }),
        );
      },
      { timeout: 2000 },
    );
  });

  it("saves the current configuration when the save button is clicked", async () => {
    const user = userEvent.setup();
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUpdateWorker.mockResolvedValue({});
    renderPage(`/workers/${EXISTING_WORKER.id}`);
    await user.click(screen.getByRole("button", { name: "Configuration" }));

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

  it("edits HTTP target template fields", async () => {
    const user = userEvent.setup();
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUpdateWorker.mockResolvedValue({});
    renderPage(`/workers/${EXISTING_WORKER.id}`);
    await user.click(screen.getByRole("button", { name: "Configuration" }));

    await user.click(screen.getByRole("button", { name: "Targets" }));
    await user.click(screen.getByRole("button", { name: "Add target" }));
    fireEvent.change(screen.getByLabelText("URL"), {
      target: { value: "https://api.example.com/orders/{{ body.order_id }}" },
    });
    await user.click(screen.getByRole("combobox", { name: "Method" }));
    await user.click(screen.getByRole("option", { name: "PATCH" }));
    fireEvent.change(screen.getByLabelText("Headers"), {
      target: { value: '{"X-Trace-Id":"{{ meta.trace_id }}"}' },
    });
    fireEvent.change(screen.getByLabelText("Query params"), {
      target: { value: '{"attempt":"{{ attempts }}","customer":"{{ body.customer_id }}"}' },
    });
    fireEvent.change(screen.getByLabelText("Body"), {
      target: { value: '{"order_id":"{{ body.order_id }}","trace":"{{ meta.trace_id }}"}' },
    });
    fireEvent.change(screen.getByLabelText("Timeout seconds"), {
      target: { value: "8" },
    });
    fireEvent.change(screen.getByLabelText("Success statuses"), {
      target: { value: "[200, 202]" },
    });
    mockUpdateWorker.mockClear();

    await user.click(screen.getByRole("button", { name: "Save configuration" }));

    expect(mockUpdateWorker).toHaveBeenCalledWith(
      expect.objectContaining({
        sink_configs: [
          {
            type: "http_sink",
            connector_id: null,
            fields: {
              url: "https://api.example.com/orders/{{ body.order_id }}",
              method: "PATCH",
              headers: {
                "X-Trace-Id": "{{ meta.trace_id }}",
              },
              params: {
                attempt: "{{ attempts }}",
                customer: "{{ body.customer_id }}",
              },
              body: {
                order_id: "{{ body.order_id }}",
                trace: "{{ meta.trace_id }}",
              },
              timeout_s: 8,
              success_statuses: [200, 202],
            },
          },
        ],
      }),
    );
  });

  it("defaults new HTTP sinks to POST", async () => {
    const user = userEvent.setup();
    mockCreateWorker.mockResolvedValue({ id: EXISTING_WORKER.id });
    renderPage();

    await user.click(screen.getByRole("button", { name: "Configuration" }));
    await user.click(screen.getByRole("button", { name: "Targets" }));
    await user.click(screen.getByRole("button", { name: "Add target" }));

    expect(screen.getByRole("combobox", { name: "Method" })).toHaveTextContent("POST");

    await user.type(screen.getByLabelText("URL"), "https://example.com/events");
    await user.click(screen.getByRole("button", { name: "Save configuration" }));

    expect(mockCreateWorker).toHaveBeenCalledWith(
      expect.objectContaining({
        sink_configs: [
          {
            type: "http_sink",
            connector_id: null,
            fields: {
              url: "https://example.com/events",
              method: "POST",
            },
          },
        ],
      }),
    );
  });

  it("filters target connectors by selected sink type", async () => {
    const user = userEvent.setup();
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUseConnectorsQuery.mockReturnValue({
      data: {
        items: [
          { id: "mysql-1", name: "mysql-main", type: "mysql" },
          { id: "redis-1", name: "redis-main", type: "redis" },
          { id: "feishu-1", name: "bitable-main", type: "feishu_bitable" },
        ],
      },
      isPending: false,
      error: null,
    });
    renderPage(`/workers/${EXISTING_WORKER.id}`);
    await user.click(screen.getByRole("button", { name: "Configuration" }));

    await user.click(screen.getByRole("button", { name: "Targets" }));
    await user.click(screen.getByRole("button", { name: "Add target" }));
    await user.click(screen.getByRole("combobox", { name: "Sink type 1" }));
    await user.click(screen.getByRole("option", { name: "MySQL Table Sink" }));
    await user.click(screen.getByRole("combobox", { name: "Connector" }));

    expect(screen.getByRole("option", { name: "mysql-main (mysql)" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "redis-main (redis)" })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "bitable-main (feishu_bitable)" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Sink type 1" }));
    await user.click(screen.getByRole("option", { name: "Feishu Bitable Table Sink" }));
    await user.click(screen.getByRole("combobox", { name: "Connector" }));

    expect(screen.getByRole("option", { name: "bitable-main (feishu_bitable)" })).toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "mysql-main (mysql)" })).not.toBeInTheDocument();
  });

  it("lists deployable agents and sends current environment variables when one is clicked", async () => {
    const user = userEvent.setup();
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUseWorkerAgentsQuery.mockReturnValue({
      data: {
        items: [
          buildAgent({ worker_agent_id: "agent-1", display_name: "prod-runner-1" }),
          buildAgent({
            worker_agent_id: "agent-full",
            display_name: "full-runner",
            used_slots: 4,
            max_concurrent_deployments: 4,
          }),
          buildAgent({
            worker_agent_id: "agent-offline",
            display_name: "offline-runner",
            status: "offline",
          }),
        ],
        total: 3,
        limit: 100,
        offset: 0,
      },
      isPending: false,
      error: null,
    });
    mockDeployWorker.mockResolvedValue({ deployment_id: "deployment-1" });
    renderPage(`/workers/${EXISTING_WORKER.id}`);

    await user.click(screen.getByRole("button", { name: "Configuration" }));
    await user.click(screen.getByRole("button", { name: "Environment variables" }));
    await user.click(screen.getByRole("button", { name: "Add variable" }));
    await user.type(screen.getByLabelText("Key 1"), "API_TOKEN");
    await user.type(screen.getByLabelText("Value 1"), "secret-token");
    await user.click(screen.getByRole("button", { name: "Deploy" }));

    expect(screen.getByRole("button", { name: /prod-runner-1/ })).toBeInTheDocument();
    expect(screen.queryByText("full-runner")).not.toBeInTheDocument();
    expect(screen.queryByText("offline-runner")).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /prod-runner-1/ }));

    expect(mockDeployWorker).toHaveBeenCalledWith({
      worker_agent_id: "agent-1",
      env: { API_TOKEN: "secret-token" },
    });
    expect(await screen.findByText("Deployment events")).toBeInTheDocument();
  });

  it("shows a deployment error when agent deployment fails", async () => {
    const user = userEvent.setup();
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUseWorkerAgentsQuery.mockReturnValue({
      data: {
        items: [buildAgent({ worker_agent_id: "agent-1", display_name: "prod-runner-1" })],
        total: 1,
        limit: 100,
        offset: 0,
      },
      isPending: false,
      error: null,
    });
    mockDeployWorker.mockRejectedValue(new Error("worker has no handler package"));
    renderPage(`/workers/${EXISTING_WORKER.id}`);

    await user.click(screen.getByRole("button", { name: "Code source" }));
    await user.click(screen.getByRole("button", { name: "Deploy to agent" }));
    await user.click(screen.getByRole("button", { name: /prod-runner-1/ }));

    expect(await screen.findByText(/Deploy failed: step has no handler package/)).toBeInTheDocument();
  });

  it("packages code and binds the package when editing", async () => {
    const user = userEvent.setup();
    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUploadPackage.mockResolvedValue({
      package_id: "99999999-9999-9999-9999-999999999999",
    });
    mockUpdateWorker.mockResolvedValue({});

    renderPage(`/workers/${EXISTING_WORKER.id}`);

    await user.click(screen.getByRole("button", { name: "Code source" }));
    expect(screen.getByRole("button", { name: "Download zip" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Package code" }));

    await waitFor(() => {
      expect(mockUploadPackage).toHaveBeenCalledWith(
        expect.objectContaining({
          entrypoint: "handler.py",
        }),
      );
    });
    expect(mockUpdateWorker).toHaveBeenCalledWith(
      expect.objectContaining({
        handler_package_id: "99999999-9999-9999-9999-999999999999",
        name: "order-sync",
        handler_ref: "handler:handler",
      }),
    );
    expect(screen.getByRole("button", { name: "Download zip" })).toBeEnabled();
  });

  it("downloads the latest packaged zip", async () => {
    const user = userEvent.setup();
    const urlApi = URL as typeof URL & {
      createObjectURL?: (blob: Blob) => string;
      revokeObjectURL?: (url: string) => void;
    };
    const originalCreateObjectURL = urlApi.createObjectURL;
    const originalRevokeObjectURL = urlApi.revokeObjectURL;
    const createObjectURL = vi.fn(() => "blob:worker-package");
    const revokeObjectURL = vi.fn();
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});

    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });

    mockUseWorkerQuery.mockReturnValue({
      data: EXISTING_WORKER,
      isPending: false,
      error: null,
    });
    mockUploadPackage.mockResolvedValue({
      package_id: "99999999-9999-9999-9999-999999999999",
    });
    mockDownloadWorkerPackage.mockResolvedValue({
      blob: new Blob(["compiled package"], { type: "application/zip" }),
      filename: "order-sync.zip",
    });
    mockUpdateWorker.mockResolvedValue({});

    try {
      renderPage(`/workers/${EXISTING_WORKER.id}`);

      await user.click(screen.getByRole("button", { name: "Code source" }));
      await user.click(screen.getByRole("button", { name: "Package code" }));

      const downloadButton = screen.getByRole("button", { name: "Download zip" });
      await waitFor(() => expect(downloadButton).toBeEnabled());
      mockUpdateWorker.mockClear();

      await user.click(downloadButton);

      expect(mockUpdateWorker).toHaveBeenCalledWith(
        expect.objectContaining({
          handler_package_id: "99999999-9999-9999-9999-999999999999",
          name: "order-sync",
        }),
      );
      expect(mockDownloadWorkerPackage).toHaveBeenCalled();
      expect(createObjectURL).toHaveBeenCalledWith(expect.any(Blob));
      expect(clickSpy).toHaveBeenCalled();
    } finally {
      if (originalCreateObjectURL) {
        Object.defineProperty(URL, "createObjectURL", {
          configurable: true,
          value: originalCreateObjectURL,
        });
      } else {
        Reflect.deleteProperty(URL, "createObjectURL");
      }
      if (originalRevokeObjectURL) {
        Object.defineProperty(URL, "revokeObjectURL", {
          configurable: true,
          value: originalRevokeObjectURL,
        });
      } else {
        Reflect.deleteProperty(URL, "revokeObjectURL");
      }
      clickSpy.mockRestore();
    }
  });
});

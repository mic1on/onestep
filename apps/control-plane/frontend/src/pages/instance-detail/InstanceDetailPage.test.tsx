import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../../components/ui/ToastProvider";
import type {
  AgentCommandSummary,
  ConsoleSessionResponse,
  InstanceDetailResponse,
  InstanceListResponse,
  UiStreamConnectionState,
} from "../../lib/api/types";
import { InstanceDetailPage } from "./InstanceDetailPage";

const mockUseConsoleSessionQuery = vi.fn();
const mockUseInstanceDetailQuery = vi.fn();
const mockUseServiceInstancesQuery = vi.fn();
const mockUseInstanceCommandsQuery = vi.fn();
const mockUseCreateInstanceCommandMutation = vi.fn();
const mockUseCommandStreamStatus = vi.fn();

vi.mock("../../features/auth/queries", () => ({
  useConsoleSessionQuery: () => mockUseConsoleSessionQuery(),
}));

vi.mock("../../features/instances/queries", () => ({
  useInstanceDetailQuery: (...args: unknown[]) => mockUseInstanceDetailQuery(...args),
}));

vi.mock("../../features/services/queries", () => ({
  useServiceInstancesQuery: (...args: unknown[]) => mockUseServiceInstancesQuery(...args),
}));

vi.mock("../../features/commands/queries", async () => {
  const actual = await vi.importActual<typeof import("../../features/commands/queries")>(
    "../../features/commands/queries",
  );
  return {
    ...actual,
    useInstanceCommandsQuery: (...args: unknown[]) => mockUseInstanceCommandsQuery(...args),
    useCreateInstanceCommandMutation: (...args: unknown[]) => mockUseCreateInstanceCommandMutation(...args),
  };
});

vi.mock("../../features/commands/useCommandStream", () => ({
  useCommandStreamStatus: () => mockUseCommandStreamStatus(),
}));

function renderPage() {
  render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/services/demo-service/instances/instance-1?environment=prod"]}>
        <Routes>
          <Route path="/services/:serviceName/instances/:instanceId" element={<InstanceDetailPage />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  );
}

function buildSession(role: ConsoleSessionResponse["role"] = "admin"): ConsoleSessionResponse {
  return {
    auth_configured: true,
    bootstrap_required: false,
    authenticated: true,
    username: "admin",
    role,
    roles: role ? [role] : [],
  };
}

function buildInstanceDetail(): InstanceDetailResponse {
  return {
    service: {
      name: "demo-service",
      environment: "prod",
      latest_deployment_version: "2026.05.15",
      latest_topology_hash: "topology-hash",
      latest_sync_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      instance_count: 1,
      online_instance_count: 1,
      last_seen_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      source_kinds: ["manual"],
      task_count: 0,
      created_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      updated_at: new Date("2026-05-15T08:00:00Z").toISOString(),
    },
    lookback_minutes: 60,
    lookback_started_at: new Date("2026-05-15T07:00:00Z").toISOString(),
    instance: {
      instance_id: "instance-1",
      node_name: "node-a",
      hostname: "node-a.local",
      pid: 321,
      deployment_version: "2026.05.15",
      onestep_version: "1.0.0",
      python_version: "3.12.3",
      started_at: new Date("2026-05-15T07:30:00Z").toISOString(),
      last_sync_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      last_topology_hash: "topology-hash",
      last_heartbeat_sent_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      last_heartbeat_sequence: 8,
      last_seen_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      status: "ok",
      connectivity: "online",
      active_session: {
        session_id: "session-1",
        instance_id: "instance-1",
        node_name: "node-a",
        hostname: "node-a.local",
        status: "active",
        protocol_version: "1",
        capabilities: ["command.restart", "command.shutdown", "command.drain", "command.ping"],
        accepted_capabilities: ["command.restart", "command.shutdown", "command.drain", "command.ping"],
        connected_at: new Date("2026-05-15T08:00:00Z").toISOString(),
        last_hello_at: new Date("2026-05-15T08:00:00Z").toISOString(),
        last_message_at: new Date("2026-05-15T08:00:00Z").toISOString(),
        superseded_at: null,
        disconnected_at: null,
        created_at: new Date("2026-05-15T08:00:00Z").toISOString(),
        updated_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      },
      created_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      updated_at: new Date("2026-05-15T08:00:00Z").toISOString(),
    },
    latest_session: {
      session_id: "session-1",
      instance_id: "instance-1",
      node_name: "node-a",
      hostname: "node-a.local",
      status: "active",
      protocol_version: "1",
      capabilities: ["command.restart", "command.shutdown", "command.drain", "command.ping"],
      accepted_capabilities: ["command.restart", "command.shutdown", "command.drain", "command.ping"],
      connected_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      last_hello_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      last_message_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      superseded_at: null,
      disconnected_at: null,
      created_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      updated_at: new Date("2026-05-15T08:00:00Z").toISOString(),
    },
    app_snapshot: { health: "ok" },
    recent_metric_windows: [],
    recent_events: [],
  };
}

function buildInstances(): InstanceListResponse {
  const detail = buildInstanceDetail();
  return {
    items: [detail.instance],
    total: 1,
    limit: 100,
    offset: 0,
  };
}

function buildStreamState(phase: UiStreamConnectionState["phase"]): UiStreamConnectionState {
  return {
    phase,
    last_connected_at: Date.now(),
    last_event_at: Date.now(),
    last_error_at: phase === "reconnecting" ? Date.now() : null,
  };
}

function buildCommands(): { data: AgentCommandSummary[] } {
  return { data: [] };
}

describe("InstanceDetailPage", () => {
  afterEach(() => {
    mockUseConsoleSessionQuery.mockReset();
    mockUseInstanceDetailQuery.mockReset();
    mockUseServiceInstancesQuery.mockReset();
    mockUseInstanceCommandsQuery.mockReset();
    mockUseCreateInstanceCommandMutation.mockReset();
    mockUseCommandStreamStatus.mockReset();
  });

  it("shows a degraded banner when live updates are reconnecting", async () => {
    mockUseConsoleSessionQuery.mockReturnValue({ data: buildSession(), isPending: false, error: null });
    mockUseCommandStreamStatus.mockReturnValue(buildStreamState("reconnecting"));
    mockUseInstanceDetailQuery.mockReturnValue({
      data: buildInstanceDetail(),
      dataUpdatedAt: Date.now(),
      isPending: false,
      error: null,
    });
    mockUseServiceInstancesQuery.mockReturnValue({ data: buildInstances() });
    mockUseInstanceCommandsQuery.mockReturnValue({
      data: { items: buildCommands().data, total: 0, limit: 50, offset: 0 },
    });
    mockUseCreateInstanceCommandMutation.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn(),
    });

    renderPage();

    expect(await screen.findByText("Live updates are reconnecting")).toBeInTheDocument();
    expect(screen.queryByText("Current lens")).not.toBeInTheDocument();
    expect(screen.getAllByText("Recent events").length).toBeGreaterThan(0);
  });

  it("requires explicit review before submitting a restart command", async () => {
    const mutateAsync = vi.fn().mockResolvedValue({
      command_id: "cmd-1",
      instance_id: "instance-1",
      node_name: "node-a",
      session_id: "session-1",
      created_by: "admin",
      reason: "Drain traffic before restart",
      source_surface: "instance_detail",
      kind: "restart",
      args: {},
      timeout_s: 30,
      status: "pending",
      ack_status: null,
      result: null,
      duration_ms: null,
      error_code: null,
      error_message: null,
      created_at: new Date("2026-05-15T08:00:00Z").toISOString(),
      dispatched_at: null,
      acked_at: null,
      finished_at: null,
      updated_at: new Date("2026-05-15T08:00:00Z").toISOString(),
    } satisfies AgentCommandSummary);

    mockUseConsoleSessionQuery.mockReturnValue({ data: buildSession(), isPending: false, error: null });
    mockUseCommandStreamStatus.mockReturnValue(buildStreamState("connected"));
    mockUseInstanceDetailQuery.mockReturnValue({
      data: buildInstanceDetail(),
      dataUpdatedAt: Date.now(),
      isPending: false,
      error: null,
    });
    mockUseServiceInstancesQuery.mockReturnValue({ data: buildInstances() });
    mockUseInstanceCommandsQuery.mockReturnValue({
      data: { items: [], total: 0, limit: 50, offset: 0 },
    });
    mockUseCreateInstanceCommandMutation.mockReturnValue({
      isPending: false,
      mutateAsync,
    });

    renderPage();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Restart" }));
    await user.type(screen.getByLabelText("Operator reason"), "Drain traffic before restart");
    await user.click(screen.getByRole("button", { name: "Review and send" }));

    expect(
      await screen.findAllByText(
        "Confirm that you reviewed the scope and impact before sending this command.",
      ),
    ).toHaveLength(2);
    expect(mutateAsync).not.toHaveBeenCalled();

    await user.click(
      screen.getByLabelText(
        "I reviewed the target scope and understand that this action may interrupt live work or discard data.",
      ),
    );
    await user.click(screen.getByRole("button", { name: "Review and send" }));

    expect(mutateAsync).toHaveBeenCalledWith({
      kind: "restart",
      args: {},
      timeout_s: 30,
      delivery_mode: "dispatch_now_only",
      reason: "Drain traffic before restart",
    });
  });
});

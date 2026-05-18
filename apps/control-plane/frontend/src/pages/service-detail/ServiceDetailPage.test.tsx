import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../../components/ui/ToastProvider";
import type {
  AgentCommandListResponse,
  AgentSessionListResponse,
  ConsoleSessionResponse,
  InstanceListResponse,
  ServiceDashboardResponse,
  TaskDashboardListResponse,
  UiStreamConnectionState,
} from "../../lib/api/types";
import { ServiceDetailPage } from "./ServiceDetailPage";

const mockUseConsoleSessionQuery = vi.fn();
const mockUseServiceDashboardQuery = vi.fn();
const mockUseServiceTasksQuery = vi.fn();
const mockUseServiceInstancesQuery = vi.fn();
const mockUseServiceCommandsQuery = vi.fn();
const mockUseServiceSessionsQuery = vi.fn();
const mockUseCreateServiceCommandFanoutMutation = vi.fn();
const mockUseCommandStreamStatus = vi.fn();

vi.mock("../../features/auth/queries", () => ({
  useConsoleSessionQuery: () => mockUseConsoleSessionQuery(),
}));

vi.mock("../../features/services/queries", () => ({
  useServiceDashboardQuery: (...args: unknown[]) => mockUseServiceDashboardQuery(...args),
  useServiceTasksQuery: (...args: unknown[]) => mockUseServiceTasksQuery(...args),
  useServiceInstancesQuery: (...args: unknown[]) => mockUseServiceInstancesQuery(...args),
}));

vi.mock("../../features/commands/queries", async () => {
  const actual = await vi.importActual<typeof import("../../features/commands/queries")>(
    "../../features/commands/queries",
  );
  return {
    ...actual,
    useServiceCommandsQuery: (...args: unknown[]) => mockUseServiceCommandsQuery(...args),
    useServiceSessionsQuery: (...args: unknown[]) => mockUseServiceSessionsQuery(...args),
    useCreateServiceCommandFanoutMutation: (...args: unknown[]) => mockUseCreateServiceCommandFanoutMutation(...args),
  };
});

vi.mock("../../features/commands/useCommandStream", () => ({
  useCommandStreamStatus: () => mockUseCommandStreamStatus(),
}));

function buildSession(): ConsoleSessionResponse {
  return {
    auth_configured: true,
    bootstrap_required: false,
    authenticated: true,
    username: "viewer",
    role: "viewer",
    roles: ["viewer"],
  };
}

function buildDashboard(): ServiceDashboardResponse {
  const now = new Date("2026-05-16T08:00:00Z").toISOString();
  return {
    service: {
      name: "demo-service",
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
    lookback_minutes: 60,
    lookback_started_at: now,
    instance_connectivity: {
      total: 4,
      online: 4,
      offline: 0,
      never_reported: 0,
    },
    instance_statuses: {
      ok: 4,
      degraded: 0,
      error: 0,
      starting: 0,
      unknown: 0,
    },
    task_count: 2,
    failing_task_count: 0,
    command_overview: {
      statuses: {
        pending: 0,
        dispatched: 0,
        accepted: 0,
        expired: 0,
        rejected: 0,
        succeeded: 8,
        failed: 0,
        timeout: 0,
        cancelled: 0,
        in_flight: 0,
        total: 8,
      },
      active_session_count: 2,
      last_command_at: now,
      last_completed_at: now,
    },
    topology_hashes: ["hash-a"],
    topology_consistent: true,
    recent_events: [],
  };
}

function buildTasks(): TaskDashboardListResponse {
  return {
    lookback_minutes: 60,
    lookback_started_at: new Date("2026-05-16T07:00:00Z").toISOString(),
    items: [],
    total: 0,
    limit: 100,
    offset: 0,
  };
}

function buildInstances(): InstanceListResponse {
  const now = new Date("2026-05-16T08:00:00Z").toISOString();
  return {
    items: [
      {
        instance_id: "instance-1",
        node_name: "node-a",
        hostname: "node-a.local",
        pid: 123,
        deployment_version: "2026.05.16",
        onestep_version: "1.0.0",
        python_version: "3.12.0",
        started_at: now,
        last_sync_at: now,
        last_topology_hash: "hash-a",
        last_heartbeat_sent_at: now,
        last_heartbeat_sequence: 1,
        last_seen_at: now,
        status: "ok",
        connectivity: "online",
        active_session: null,
        created_at: now,
        updated_at: now,
      },
    ],
    total: 1,
    limit: 100,
    offset: 0,
  };
}

function buildCommands(): AgentCommandListResponse {
  return {
    items: [],
    total: 0,
    limit: 50,
    offset: 0,
  };
}

function buildSessions(): AgentSessionListResponse {
  const now = new Date("2026-05-16T08:00:00Z").toISOString();
  return {
    items: [
      {
        session_id: "session-1",
        instance_id: "instance-1",
        node_name: "node-a",
        hostname: "node-a.local",
        status: "active",
        protocol_version: "1",
        capabilities: ["command.restart"],
        accepted_capabilities: ["command.restart"],
        connected_at: now,
        last_hello_at: now,
        last_message_at: now,
        superseded_at: null,
        disconnected_at: null,
        created_at: now,
        updated_at: now,
      },
    ],
    total: 1,
    limit: 50,
    offset: 0,
  };
}

function buildStreamState(): UiStreamConnectionState {
  return {
    phase: "connected",
    last_connected_at: Date.now(),
    last_event_at: Date.now(),
    last_error_at: null,
  };
}

function renderPage() {
  render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/services/demo-service?environment=prod"]}>
        <Routes>
          <Route path="/services/:serviceName" element={<ServiceDetailPage />} />
        </Routes>
      </MemoryRouter>
    </ToastProvider>,
  );
}

describe("ServiceDetailPage", () => {
  afterEach(() => {
    mockUseConsoleSessionQuery.mockReset();
    mockUseServiceDashboardQuery.mockReset();
    mockUseServiceTasksQuery.mockReset();
    mockUseServiceInstancesQuery.mockReset();
    mockUseServiceCommandsQuery.mockReset();
    mockUseServiceSessionsQuery.mockReset();
    mockUseCreateServiceCommandFanoutMutation.mockReset();
    mockUseCommandStreamStatus.mockReset();
  });

  it("shows the topbar metric and overview panels without the service hero", async () => {
    mockUseConsoleSessionQuery.mockReturnValue({ data: buildSession(), isPending: false, error: null });
    mockUseServiceDashboardQuery.mockReturnValue({
      data: buildDashboard(),
      dataUpdatedAt: Date.now(),
      isPending: false,
      error: null,
    });
    mockUseServiceTasksQuery.mockReturnValue({ data: buildTasks(), isPending: false, error: null });
    mockUseServiceInstancesQuery.mockReturnValue({ data: buildInstances(), isPending: false, error: null });
    mockUseServiceCommandsQuery.mockReturnValue({ data: buildCommands(), isPending: false, error: null });
    mockUseServiceSessionsQuery.mockReturnValue({ data: buildSessions(), isPending: false, error: null });
    mockUseCreateServiceCommandFanoutMutation.mockReturnValue({
      isPending: false,
      mutateAsync: vi.fn(),
    });
    mockUseCommandStreamStatus.mockReturnValue(buildStreamState());

    renderPage();

    expect(await screen.findByRole("heading", { name: "demo-service" })).toBeInTheDocument();
    expect(screen.queryByText("Current lens")).not.toBeInTheDocument();
    expect(screen.getAllByText("Online instances").length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: "Overview" })).toBeInTheDocument();
    expect(screen.getByText("Basic information")).toBeInTheDocument();
  });
});

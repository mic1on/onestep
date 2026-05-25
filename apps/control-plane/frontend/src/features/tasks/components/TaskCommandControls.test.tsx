import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../../../components/ui/ToastProvider";
import type { TaskControlStateSummary, TaskInstanceControlState } from "../../../lib/api/types";
import { TaskCommandControls } from "./TaskCommandControls";

const mockMutateAsync = vi.fn();

vi.mock("../../commands/queries", () => ({
  useCreateTaskCommandFanoutMutation: () => ({
    mutateAsync: (...args: unknown[]) => mockMutateAsync(...args),
    isPending: false,
  }),
}));

function buildTaskControl(instances?: TaskInstanceControlState[]): TaskControlStateSummary {
  return {
    task_name: "nightly-reconcile",
    instances: instances ?? [
      {
        instance_id: "inst_a",
        node_name: "worker-a",
        connectivity: "online",
        status: "ok",
        last_seen_at: "2026-05-18T10:00:00Z",
        supported_commands: ["run_task_once", "pause_task", "resume_task"],
        state_known: true,
        pause_requested: false,
        paused: false,
        accepting_new_work: true,
        runner_count: 2,
        parked_runner_count: 0,
        fetching_runner_count: 1,
        inflight_task_count: 4,
      },
    ],
  };
}

function renderControls(taskControl = buildTaskControl()) {
  render(
    <ToastProvider>
      <TaskCommandControls
        environment="prod"
        onIssueChange={undefined}
        serviceName="billing-sync"
        taskControl={taskControl}
        taskName="nightly-reconcile"
      />
    </ToastProvider>,
  );
}

describe("TaskCommandControls", () => {
  afterEach(() => {
    mockMutateAsync.mockReset();
  });

  it("opens a target-first manual run dialog without duplicating the body heading", async () => {
    renderControls();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "Run task once" }));

    const dialog = await screen.findByRole("dialog", { name: "Manual run" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getAllByText("Manual run")).toHaveLength(1);
    expect(
      screen.getByText("Dispatch a custom JSON payload to 1 supported online instance."),
    ).toBeInTheDocument();

    const payloadLabel = screen.getByText("JSON payload");
    const reasonLabel = screen.getByText("Operator reason");
    expect(payloadLabel.compareDocumentPosition(reasonLabel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const payloadTextarea = screen.getByLabelText("JSON payload");
    expect(payloadTextarea).toHaveAttribute("rows", "12");
  });

  it("distinguishes paused, working, and idle from live task execution state", async () => {
    renderControls(
      buildTaskControl([
        {
          instance_id: "inst_a",
          node_name: "worker-a",
          connectivity: "online",
          status: "ok",
          last_seen_at: "2026-05-18T10:00:00Z",
          supported_commands: ["run_task_once", "pause_task", "resume_task"],
          state_known: true,
          pause_requested: false,
          paused: false,
          accepting_new_work: true,
          runner_count: 2,
          parked_runner_count: 0,
          fetching_runner_count: 0,
          inflight_task_count: 3,
        },
        {
          instance_id: "inst_b",
          node_name: "worker-b",
          connectivity: "online",
          status: "starting",
          last_seen_at: "2026-05-18T10:00:00Z",
          supported_commands: ["run_task_once", "pause_task", "resume_task"],
          state_known: true,
          pause_requested: false,
          paused: false,
          accepting_new_work: true,
          runner_count: 2,
          parked_runner_count: 0,
          fetching_runner_count: 1,
          inflight_task_count: 0,
        },
        {
          instance_id: "inst_c",
          node_name: "worker-c",
          connectivity: "online",
          status: "ok",
          last_seen_at: "2026-05-18T10:00:00Z",
          supported_commands: ["run_task_once", "pause_task", "resume_task"],
          state_known: true,
          pause_requested: true,
          paused: true,
          accepting_new_work: false,
          runner_count: 2,
          parked_runner_count: 2,
          fetching_runner_count: 0,
          inflight_task_count: 0,
        },
      ]),
    );
    const user = userEvent.setup();

    const pausedCard = screen.getByText("Paused").closest("article");
    const workingCard = screen.getByText("Working").closest("article");
    const idleCard = screen.getByText("Idle").closest("article");
    expect(pausedCard).not.toBeNull();
    expect(workingCard).not.toBeNull();
    expect(idleCard).not.toBeNull();
    expect(within(pausedCard as HTMLElement).getByText("1")).toBeInTheDocument();
    expect(within(workingCard as HTMLElement).getByText("1")).toBeInTheDocument();
    expect(within(idleCard as HTMLElement).getByText("1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Run task once" }));

    const dialog = await screen.findByRole("dialog", { name: "Manual run" });
    const workingRow = screen.getByText("worker-a").closest("label");
    const idleStartingRow = screen.getByText("worker-b").closest("label");
    const idlePausedRow = screen.getByText("worker-c").closest("label");
    expect(workingRow).not.toBeNull();
    expect(idleStartingRow).not.toBeNull();
    expect(idlePausedRow).not.toBeNull();
    expect(within(workingRow as HTMLElement).getByText("Working")).toBeInTheDocument();
    expect(within(idleStartingRow as HTMLElement).getByText("Idle")).toBeInTheDocument();
    expect(within(idlePausedRow as HTMLElement).getByText("Paused")).toBeInTheDocument();
    expect(dialog).toBeInTheDocument();
  });
});

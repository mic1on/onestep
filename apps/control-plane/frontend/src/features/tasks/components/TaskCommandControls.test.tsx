import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ToastProvider } from "../../../components/ui/ToastProvider";
import type { TaskControlStateSummary } from "../../../lib/api/types";
import { TaskCommandControls } from "./TaskCommandControls";

const mockMutateAsync = vi.fn();

vi.mock("../../commands/queries", () => ({
  useCreateTaskCommandFanoutMutation: () => ({
    mutateAsync: (...args: unknown[]) => mockMutateAsync(...args),
    isPending: false,
  }),
}));

function buildTaskControl(): TaskControlStateSummary {
  return {
    task_name: "nightly-reconcile",
    instances: [
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

function renderControls() {
  render(
    <ToastProvider>
      <TaskCommandControls
        environment="prod"
        onIssueChange={undefined}
        serviceName="billing-sync"
        taskControl={buildTaskControl()}
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
});

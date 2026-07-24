# Task Manual Run Dialog Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the task manual run dialog smaller and visually consistent with the current Signal Console design while preserving a target-first flow and a tall JSON payload editor.

**Architecture:** Keep `OverflowDialog` as the interaction container and keep `TaskCommandControls` as the behavioral owner. Add one focused component test to lock the dialog structure, then make a surgical JSX cleanup in `TaskCommandControls.tsx` and move the manual-run-specific visual treatment into `nothing-signal-console.css` so the dialog no longer depends on the old Apple-workbench look.

**Tech Stack:** React, TypeScript, i18next, Vitest, Testing Library, CSS

---

### Task 1: Add a manual run dialog structure test

**Files:**
- Create: `frontend/src/features/tasks/components/TaskCommandControls.test.tsx`
- Test: `frontend/src/features/tasks/components/TaskCommandControls.tsx`

- [ ] **Step 1: Write the failing dialog structure test**

```tsx
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

    const dialog = await screen.findByRole("dialog", { name: "Run task once" });
    expect(dialog).toBeInTheDocument();
    expect(screen.getAllByText("Run task once")).toHaveLength(1);
    expect(screen.getByText("Choose target instances and provide a JSON payload for 1 online instance.")).toBeInTheDocument();

    const payloadLabel = screen.getByText("JSON payload");
    const reasonLabel = screen.getByText("Operator reason");
    expect(payloadLabel.compareDocumentPosition(reasonLabel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    const payloadTextarea = screen.getByLabelText("JSON payload");
    expect(payloadTextarea).toHaveAttribute("rows", "12");
  });
});
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `pnpm --dir frontend exec vitest run src/features/tasks/components/TaskCommandControls.test.tsx`
Expected: FAIL because the current dialog still renders a duplicated body heading and the payload textarea does not use the taller editor rows

- [ ] **Step 3: Commit the failing test checkpoint**

```bash
git add frontend/src/features/tasks/components/TaskCommandControls.test.tsx
git commit -m "test: cover task manual run dialog structure"
```

### Task 2: Implement the compact Signal Console manual run dialog

**Files:**
- Modify: `frontend/src/features/tasks/components/TaskCommandControls.tsx`
- Modify: `frontend/src/styles/nothing-signal-console.css`
- Modify: `frontend/src/features/tasks/components/TaskCommandControls.test.tsx`

- [ ] **Step 1: Remove the duplicated body heading and add small section wrappers**

```tsx
<OverflowDialog
  className="detail-dialog-card task-manual-run-dialog"
  description={t("taskCommandControls.manualRun.subtitle", { count: manualRunStates.length })}
  onClose={() => {
    if (mutation.isPending) {
      return;
    }
    setManualRunOpen(false);
  }}
  open={manualRunOpen}
  title={t("taskCommandControls.manualRun.title")}
>
  <div className="task-manual-run-dialog-body">
    <section className="task-manual-run-dialog-section task-manual-run-dialog-section-targets">
      <div className="task-manual-run-dialog-toolbar">
        <div className="fanout-inline-actions">
          <button
            className="button-link"
            onClick={() => setManualRunTargetIds(manualRunStates.map((state) => state.instance_id))}
            type="button"
          >
            {t("taskCommandControls.manualRun.selectAll")}
          </button>
          <button className="button-link" onClick={() => setManualRunTargetIds([])} type="button">
            {t("taskCommandControls.manualRun.clearSelection")}
          </button>
        </div>
      </div>
      <div className="task-manual-instance-list">
        {manualRunStates.map((state) => {
          const checked = manualRunTargetIds.includes(state.instance_id);
          return (
            <label
              className={checked ? "task-manual-instance-row active" : "task-manual-instance-row"}
              key={state.instance_id}
            >
              <span className="task-manual-instance-check" aria-hidden="true">
                {checked ? "✓" : ""}
              </span>
              <input checked={checked} onChange={() => toggleManualRunTarget(state.instance_id)} type="checkbox" />
              <div className="task-manual-instance-copy">
                <strong>{state.node_name}</strong>
                <p>{formatIdentifierPreview(state.instance_id)}</p>
              </div>
              <div className="task-manual-instance-state">
                <span>{state.accepting_new_work ? t("taskCommandControls.summary.accepting") : t("taskCommandControls.summary.paused")}</span>
                <span>{state.runner_count ?? 0}/{state.inflight_task_count ?? 0}</span>
              </div>
            </label>
          );
        })}
      </div>
    </section>

    <section className="task-manual-run-dialog-section task-manual-run-dialog-section-payload">
      <label className="dialog-field task-manual-run-field">
        <span>{t("taskCommandControls.manualRun.payloadLabel")}</span>
        <textarea
          className="task-manual-run-payload"
          onChange={(event) => setManualRunPayloadText(event.target.value)}
          placeholder={t("taskCommandControls.manualRun.payloadPlaceholder")}
          rows={12}
          value={manualRunPayloadText}
        />
      </label>
    </section>

    <section className="task-manual-run-dialog-section task-manual-run-dialog-section-reason">
      <label className="dialog-field task-manual-run-field">
        <span>{t("taskCommandControls.manualRun.reasonLabel")}</span>
        <textarea
          className="task-manual-run-reason"
          onChange={(event) => setManualRunReason(event.target.value)}
          placeholder={t("taskCommandControls.manualRun.reasonPlaceholder")}
          rows={3}
          value={manualRunReason}
        />
      </label>
    </section>

    {manualRunError || submitError ? (
      <p className="fanout-note inline-feedback inline-feedback-error">{manualRunError ?? submitError}</p>
    ) : null}

    <div className="dialog-actions task-manual-run-actions">
      <button
        className="button-link"
        disabled={mutation.isPending}
        onClick={() => setManualRunOpen(false)}
        type="button"
      >
        {t("commandReasonDialog.cancel")}
      </button>
      <button
        className="button-secondary"
        disabled={mutation.isPending}
        onClick={() => void handleManualRunSubmit()}
        type="button"
      >
        {mutation.isPending && submittingAction?.kind === "run_task_once"
          ? t("commands.dispatchingAction", {
              kind: t("commandKind.run_task_once", { defaultValue: "run_task_once" }),
            })
          : t("commands.action.run_task_once")}
      </button>
    </div>
  </div>
</OverflowDialog>
```

- [ ] **Step 2: Replace the legacy manual-run-specific styles with Signal Console overrides**

```css
.task-manual-run-dialog {
  width: min(100%, 46rem);
  max-height: min(84vh, 48rem);
  border-color: var(--nd-border);
  border-radius: 24px;
  background:
    radial-gradient(circle at top right, rgba(215, 25, 33, 0.05), transparent 20%),
    var(--nd-surface);
  box-shadow: none;
}

.task-manual-run-dialog .detail-dialog-heading {
  padding-bottom: 10px;
  border-bottom: 1px solid var(--nd-border);
}

.task-manual-run-dialog .dialog-copy h3 {
  color: var(--nd-text);
  font-family: "Space Grotesk", sans-serif;
  font-size: 20px;
  font-weight: 500;
}

.task-manual-run-dialog .dialog-copy p,
.task-manual-run-dialog .dialog-field span,
.task-manual-run-dialog .task-manual-instance-copy p {
  color: var(--nd-text-secondary);
}

.task-manual-run-dialog-body {
  display: grid;
  gap: 14px;
}

.task-manual-run-dialog-section {
  display: grid;
  gap: 10px;
}

.task-manual-run-dialog-toolbar {
  display: flex;
  justify-content: flex-end;
}

.task-manual-instance-row {
  min-height: 52px;
  padding: 8px 10px;
  border-color: var(--nd-border);
  border-radius: 14px;
  background: var(--nd-surface-strong);
}

.task-manual-instance-row.active {
  border-color: var(--nd-text);
  background: rgba(0, 0, 0, 0.03);
}

.task-manual-instance-check {
  border-color: var(--nd-border-strong);
  background: var(--nd-surface);
  color: var(--nd-text);
}

.task-manual-instance-row.active .task-manual-instance-check {
  border-color: var(--nd-text);
  background: var(--nd-text);
  color: var(--nd-surface);
}

.task-manual-instance-state span {
  border-color: var(--nd-border);
  background: transparent;
  color: var(--nd-text-secondary);
  font-family: "Space Mono", monospace;
  font-size: 11px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.task-manual-run-field textarea {
  border-color: var(--nd-border);
  border-radius: 16px;
  background: var(--nd-surface-strong);
  color: var(--nd-text);
}

.task-manual-run-payload {
  min-height: 15rem;
  font-family: "Space Mono", monospace;
  line-height: 1.5;
}

.task-manual-run-reason {
  min-height: 5.5rem;
}

.task-manual-run-actions {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
}

@media (max-width: 720px) {
  .task-manual-run-dialog {
    width: min(100%, calc(100vw - 24px));
  }

  .task-manual-instance-row {
    grid-template-columns: 28px minmax(0, 1fr);
  }

  .task-manual-instance-state {
    grid-column: 2;
    justify-self: start;
  }

  .task-manual-run-actions {
    grid-template-columns: minmax(0, 1fr);
  }
}
```

- [ ] **Step 3: Run the focused component test to verify it passes**

Run: `pnpm --dir frontend exec vitest run src/features/tasks/components/TaskCommandControls.test.tsx`
Expected: PASS

- [ ] **Step 4: Run a frontend build smoke test**

Run: `pnpm --dir frontend build`
Expected: PASS

- [ ] **Step 5: Commit the implementation**

```bash
git add frontend/src/features/tasks/components/TaskCommandControls.tsx frontend/src/features/tasks/components/TaskCommandControls.test.tsx frontend/src/styles/nothing-signal-console.css
git commit -m "feat: tighten task manual run dialog"
```

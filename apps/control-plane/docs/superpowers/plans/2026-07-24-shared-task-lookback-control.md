# Shared Task Lookback Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make task metrics and task events use one lookback selector with identical preset, custom-mode, and draft-retention behavior.

**Architecture:** A new `LookbackControl` component owns all selector state, normalization, and markup. `ResourceChart` and `TaskEventDiagnostics` retain only domain data and pass their existing constants, accessible labels, values, and callbacks into the shared control.

**Tech Stack:** React 19, TypeScript, Tailwind CSS v4, Vitest, Testing Library, Playwright, Vite.

---

## File Structure

- Create: `frontend/src/components/LookbackControl.tsx` - shared selector behavior and markup.
- Create: `frontend/src/components/LookbackControl.test.tsx` - focused shared behavior coverage.
- Modify: `frontend/src/components/ResourceChart.tsx` - replace local selector implementation.
- Modify: `frontend/src/components/ResourceChart.test.tsx` - retain metrics callback integration coverage.
- Modify: `frontend/src/components/TaskEventDiagnostics.tsx` - replace old always-visible custom form.
- Modify: `frontend/src/components/TaskEventDiagnostics.test.tsx` - retain events callback integration coverage.
- Modify: `frontend/e2e/app.spec.ts` - verify both selectors expand and collapse independently.

### Task 1: Define Shared Behavior With A Failing Test

**Files:**
- Create: `frontend/src/components/LookbackControl.test.tsx`

- [x] **Step 1: Add a shared interaction test**

Render `LookbackControl` with `[5, 10, 15, 30]`, a maximum of `1440`, an applied value of `15`, and a mock callback. Assert the editor is initially absent, `Custom` reveals it, applying `45` emits `45`, selecting `30m` hides it, and reopening restores `45`.

- [x] **Step 2: Run the test and verify failure**

Run: `cd frontend && pnpm exec vitest run src/components/LookbackControl.test.tsx`

Expected: FAIL because `LookbackControl.tsx` does not exist.

### Task 2: Implement The Shared Control

**Files:**
- Create: `frontend/src/components/LookbackControl.tsx`

- [x] **Step 1: Define the component contract**

```tsx
interface LookbackControlProps {
  ariaLabel: string;
  lookbackMinutes: number;
  maxLookbackMinutes: number;
  presets: readonly number[];
  onLookbackMinutesChange: (minutes: number) => void;
}
```

- [x] **Step 2: Move selector state and behavior into the component**

Use `isCustomLookbackOpen` and `customLookbackValue` state. Presets close custom mode without changing the draft. Custom submission truncates and clamps finite values to `1..maxLookbackMinutes`, keeps the editor open, and calls the callback only when the normalized value differs from the applied value.

- [x] **Step 3: Move the shared markup unchanged**

Render the preset group, `Custom` as its final button, and the conditional form in the same wrapping flex row. Use `ariaLabel` for the group and existing `chart.*` translations for Custom, Lookback minutes, Minutes, and Apply.

- [x] **Step 4: Run the shared test**

Run: `cd frontend && pnpm exec vitest run src/components/LookbackControl.test.tsx`

Expected: PASS.

### Task 3: Migrate Both Consumers

**Files:**
- Modify: `frontend/src/components/ResourceChart.tsx`
- Modify: `frontend/src/components/TaskEventDiagnostics.tsx`
- Modify: `frontend/src/components/ResourceChart.test.tsx`
- Modify: `frontend/src/components/TaskEventDiagnostics.test.tsx`

- [x] **Step 1: Replace the metrics selector**

Remove `ResourceChart`'s custom-mode state, custom draft, normalization, submit handler, and selector JSX. Render:

```tsx
<LookbackControl
  ariaLabel={`${t('chart.taskMetrics')} ${t('chart.lookbackMinutes')}`}
  lookbackMinutes={lookbackMinutes}
  maxLookbackMinutes={MAX_TASK_METRIC_LOOKBACK_MINUTES}
  presets={TASK_METRIC_LOOKBACK_PRESETS}
  onLookbackMinutesChange={onLookbackMinutesChange}
/>
```

- [x] **Step 2: Replace the task-events selector**

Remove `TaskEventDiagnostics`' custom draft, synchronization effect, normalization, submit handler, and selector JSX. Render the same component with the task-events label and `MAX_TASK_EVENT_LOOKBACK_MINUTES` / `TASK_EVENT_LOOKBACK_PRESETS`.

- [x] **Step 3: Update consumer integration tests**

Keep the metrics test focused on applying `45` through the shared control. Change the task-events test to assert its editor is initially absent, opens through `Custom`, emits `45`, closes on `30m`, and retains `45` when reopened.

- [x] **Step 4: Run all three focused test files**

Run: `cd frontend && pnpm exec vitest run src/components/LookbackControl.test.tsx src/components/ResourceChart.test.tsx src/components/TaskEventDiagnostics.test.tsx`

Expected: PASS.

### Task 4: Verify Both Controls In The Browser

**Files:**
- Modify: `frontend/e2e/app.spec.ts`

- [x] **Step 1: Extend the existing task-detail scenario**

Locate both groups by their distinct accessible names. For each group, assert `Custom` is visible and its adjacent spinbutton is absent, click `Custom` and assert the spinbutton appears, then click `30m` and assert it disappears.

- [x] **Step 2: Run E2E and build**

Run: `cd frontend && pnpm exec playwright test e2e/app.spec.ts --grep "loads API-backed service"`

Run: `cd frontend && pnpm build`

Expected: both commands pass.

- [x] **Step 3: Rebuild and restart the plane**

Run `docker compose build plane`, `docker compose up -d plane`, and `docker compose ps plane` from the repository root.

Expected: the rebuilt `plane` container reaches `healthy` and serves both corrected controls.

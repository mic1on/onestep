# Task Metrics Custom Lookback Toggle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Place `Custom` beside the task-metrics lookback presets and reveal its retained minutes editor only while custom mode is selected.

**Architecture:** Keep all new state local to `ResourceChart`: one boolean controls custom-mode visibility and the existing string stores the retained draft. Preset selection closes custom mode and calls the existing callback without overwriting the draft; custom submission keeps the existing normalization and callback contract.

**Tech Stack:** React 19, TypeScript, Tailwind CSS v4, Vitest, Testing Library, Playwright, Vite.

---

## File Structure

- Modify: `frontend/src/components/ResourceChart.test.tsx` - focused interaction and draft-retention coverage.
- Modify: `frontend/src/components/ResourceChart.tsx` - local custom-mode state and conditional form layout.
- Modify: `frontend/e2e/app.spec.ts` - task-detail regression coverage for collapsed and expanded states.

### Task 1: Add Failing Component Coverage

**Files:**
- Modify: `frontend/src/components/ResourceChart.test.tsx`

- [x] **Step 1: Replace the existing custom lookback test with collapsed-mode and retention coverage**

Add one test that initially expects no `Lookback minutes` input or `Apply` button, clicks the `Custom` button, applies `45`, selects `30m`, then reopens custom mode and expects the input value to remain `45`.

```tsx
it('reveals and retains the custom minutes editor only in custom mode', () => {
  const handleLookbackChange = vi.fn();
  const { getByLabelText, getByRole, queryByLabelText, queryByRole } = renderChart(
    zeroFailureWindows,
    15,
    handleLookbackChange,
  );

  expect(queryByLabelText('Lookback minutes')).toBeNull();
  expect(queryByRole('button', { name: 'Apply' })).toBeNull();

  fireEvent.click(getByRole('button', { name: 'Custom' }));
  expect(getByRole('button', { name: 'Custom' })).toHaveAttribute('aria-pressed', 'true');

  fireEvent.change(getByLabelText('Lookback minutes'), { target: { value: '45' } });
  fireEvent.click(getByRole('button', { name: 'Apply' }));
  expect(handleLookbackChange).toHaveBeenCalledWith(45);
  expect(getByLabelText('Lookback minutes')).toBeInTheDocument();

  fireEvent.click(getByRole('button', { name: '30m' }));
  expect(handleLookbackChange).toHaveBeenCalledWith(30);
  expect(queryByLabelText('Lookback minutes')).toBeNull();

  fireEvent.click(getByRole('button', { name: 'Custom' }));
  expect(getByLabelText('Lookback minutes')).toHaveValue(45);
});
```

- [x] **Step 2: Run the focused test and verify failure**

Run: `cd frontend && pnpm exec vitest run src/components/ResourceChart.test.tsx`

Expected: FAIL because the custom form is currently visible before `Custom` exists.

### Task 2: Implement Custom Mode

**Files:**
- Modify: `frontend/src/components/ResourceChart.tsx`

- [x] **Step 1: Add custom-mode state and separate preset selection from custom submission**

Add `isCustomLookbackOpen`, remove the effect that overwrites `customLookbackValue` whenever the applied lookback changes, and add this preset handler:

```tsx
const [isCustomLookbackOpen, setIsCustomLookbackOpen] = useState(false);

const applyPresetLookback = (minutes: number) => {
  setIsCustomLookbackOpen(false);
  if (minutes !== lookbackMinutes) {
    onLookbackMinutesChange(minutes);
  }
};
```

Keep normalization in `applyLookbackMinutes` for custom submission, including updating the draft to the normalized value.

- [x] **Step 2: Add `Custom` to the preset group**

Preset buttons use `aria-pressed={!isCustomLookbackOpen && lookbackMinutes === minutes}` and call `applyPresetLookback(minutes)`. Append this button after the preset map:

```tsx
<button
  type="button"
  aria-pressed={isCustomLookbackOpen}
  onClick={() => setIsCustomLookbackOpen(true)}
  className={`h-6 rounded px-2 transition-colors ${
    isCustomLookbackOpen
      ? 'bg-indigo-50 text-indigo-600'
      : 'text-slate-500 hover:bg-slate-50 hover:text-slate-700'
  }`}
>
  {t('chart.customLookback')}
</button>
```

- [x] **Step 3: Render the existing form conditionally**

Wrap the unchanged form in `isCustomLookbackOpen && (...)`. Keep it beside the button group in the existing wrapping flex row and remove the old standalone `Custom` text label from the form.

- [x] **Step 4: Run focused tests**

Run: `cd frontend && pnpm exec vitest run src/components/ResourceChart.test.tsx`

Expected: PASS.

### Task 3: Add Task-Detail Browser Regression

**Files:**
- Modify: `frontend/e2e/app.spec.ts`

- [x] **Step 1: Extend the existing API-backed task-detail scenario**

After locating `taskMetricsLookback`, assert the custom editor is absent, click `Custom`, assert the adjacent editor appears, then select `30m` and assert it disappears:

```ts
const taskMetricsLookbackControls = taskMetricsLookback.locator('xpath=..');
await expect(taskMetricsLookback.getByRole("button", { name: "Custom", pressed: false })).toBeVisible();
await expect(taskMetricsLookbackControls.getByLabel("Lookback minutes")).toHaveCount(0);
await taskMetricsLookback.getByRole("button", { name: "Custom" }).click();
await expect(taskMetricsLookbackControls.getByLabel("Lookback minutes")).toBeVisible();
await taskMetricsLookback.getByRole("button", { name: "30m" }).click();
await expect(taskMetricsLookbackControls.getByLabel("Lookback minutes")).toHaveCount(0);
```

- [x] **Step 2: Run the focused E2E scenario**

Run: `cd frontend && pnpm exec playwright test e2e/app.spec.ts --grep "loads API-backed service"`

Expected: PASS.

### Task 4: Validate And Refresh The Running Plane

**Files:**
- Modify: `frontend/src/components/ResourceChart.test.tsx`
- Modify: `frontend/src/components/ResourceChart.tsx`
- Modify: `frontend/e2e/app.spec.ts`

- [x] **Step 1: Run focused tests and the production build**

Run: `cd frontend && pnpm exec vitest run src/components/ResourceChart.test.tsx && pnpm build`

Expected: all component tests pass and Vite produces the production bundle without TypeScript errors.

- [x] **Step 2: Rebuild and restart the control-plane image**

Run from the repository root:

```bash
docker compose build plane
docker compose up -d plane
docker compose ps plane
```

Expected: `plane` is running and reaches healthy status with the new frontend assets.

- [x] **Step 3: Visually verify desktop and mobile layouts**

Open the task-detail page at desktop and 390px mobile widths. Verify `Custom` immediately follows `30m`, the editor is absent until clicked, the full editor does not overlap adjacent controls, selecting `30m` collapses it, and reopening custom mode preserves the draft.

- [x] **Step 4: Review the final diff**

Run: `git diff --check && git status --short`

Expected: no whitespace errors and only the planned feature, test, E2E, and plan files are changed.

## Self-Review

- Every approved interaction is covered by Task 1 or Task 2.
- The existing numeric bounds and normalized submission behavior remain unchanged.
- The plan introduces no API, translation, or unrelated component changes.
- Every code change has an exact file, expected test result, and validation command.

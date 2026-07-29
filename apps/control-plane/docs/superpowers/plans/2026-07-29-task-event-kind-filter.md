# Task Event Kind Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an accurate server-side multi-select kind filter to paginated task event history.

**Architecture:** The API accepts repeated `kind` query parameters using one history-kind literal shared by endpoint validation and query filtering. React keeps selected kinds in `App`, serializes arrays as repeated parameters, and renders a grouped checkbox popover in `TaskEventDiagnostics`; an empty selection means all kinds.

**Tech Stack:** FastAPI, SQLAlchemy, Pydantic, React 19, TypeScript, Tailwind CSS, Vitest, Testing Library, pytest, Docker Compose.

---

### Task 1: Filter The Backend History Union

**Files:**
- Modify: `apps/control-plane/backend/src/onestep_control_plane_api/api/schemas.py`
- Modify: `apps/control-plane/backend/src/onestep_control_plane_api/api/routers/query.py`
- Modify: `apps/control-plane/backend/src/onestep_control_plane_api/api/query_support.py`
- Test: `apps/control-plane/backend/tests/test_query_api.py`

- [ ] **Step 1: Write failing API tests**

Extend `test_list_service_task_events_merges_runtime_events_and_control_commands` with repeated query parameters and assertions for runtime-only, command-only, mixed, paginated, and invalid filters:

```python
filtered = client.get(
    "/api/v1/services/billing-sync/tasks/sync_users/events",
    params=[
        ("environment", "prod"),
        ("lookback_minutes", "15"),
        ("kind", "succeeded"),
        ("kind", "restart_task"),
    ],
)
assert filtered.status_code == 200
assert filtered.json()["total"] == 2
assert [item["kind"] for item in filtered.json()["items"]] == [
    "restart_task",
    "succeeded",
]

invalid = client.get(
    "/api/v1/services/billing-sync/tasks/sync_users/events",
    params={"environment": "prod", "kind": "unknown"},
)
assert invalid.status_code == 422
```

- [ ] **Step 2: Verify the tests fail**

Run:

```bash
uv run pytest apps/control-plane/backend/tests/test_query_api.py -k task_events -v
```

Expected: the filtered request still returns all five seeded events and the invalid request is accepted.

- [ ] **Step 3: Define and apply the validated kind list**

Add a `TaskEventHistoryKind` literal containing the six runtime kinds and six task-command kinds. Accept `kind: list[TaskEventHistoryKind] = Query(default=[])` in `list_service_task_events`, pass it to `build_task_event_history_items`, and filter each source before merging:

```python
runtime_kinds = set(kinds).intersection(TASK_EVENT_KINDS)
command_kinds = set(kinds).intersection(TASK_EVENT_HISTORY_COMMAND_KINDS)

if kinds:
    runtime_filters.append(TaskEvent.kind.in_(runtime_kinds))
    command_filters.append(AgentCommand.kind.in_(command_kinds))
```

Use source-specific false predicates when the corresponding intersection is empty, so command-only filters cannot leak runtime rows and vice versa.

- [ ] **Step 4: Verify backend tests pass**

Run the focused command from Step 2. Expected: all selected task-event API tests pass.

- [ ] **Step 5: Commit the backend slice**

```bash
git add apps/control-plane/backend/src/onestep_control_plane_api/api/{schemas.py,query_support.py,routers/query.py} apps/control-plane/backend/tests/test_query_api.py
git commit -m "feat: filter task event history by kind"
```

### Task 2: Serialize Repeated Query Parameters

**Files:**
- Modify: `apps/control-plane/frontend/src/api.ts`
- Test: `apps/control-plane/frontend/src/api.controlPlaneData.test.ts`

- [ ] **Step 1: Write a failing frontend API test**

Call `loadTaskEventLogs` with `kinds: ['failed', 'restart_task']` and assert both appear independently:

```typescript
const url = new URL(String(fetchMock.mock.calls[0][0]));
expect(url.searchParams.getAll('kind')).toEqual(['failed', 'restart_task']);
```

- [ ] **Step 2: Verify the API test fails**

Run:

```bash
npm test -- --run src/api.controlPlaneData.test.ts
```

from `apps/control-plane/frontend`. Expected: the `kind` values are absent or collapsed.

- [ ] **Step 3: Add the history kind contract and array serialization**

Export `TaskEventHistoryKind`, extend `QueryValue` to `string | number | readonly (string | number)[] | undefined | null`, and update `buildApiUrl` to call `searchParams.append` for every array element. Add `kinds?: readonly TaskEventHistoryKind[]` to `loadTaskEventLogs` and pass it as `kind: kinds`.

```typescript
if (Array.isArray(value)) {
  value.forEach((item) => url.searchParams.append(key, String(item)));
  continue;
}
url.searchParams.set(key, String(value));
```

- [ ] **Step 4: Verify the API test passes**

Run the focused Vitest command from Step 2. Expected: all API tests pass.

- [ ] **Step 5: Commit the API slice**

```bash
git add apps/control-plane/frontend/src/api.ts apps/control-plane/frontend/src/api.controlPlaneData.test.ts
git commit -m "feat: query task events by multiple kinds"
```

### Task 3: Build The Grouped Multi-Select Control

**Files:**
- Modify: `apps/control-plane/frontend/src/components/TaskEventDiagnostics.tsx`
- Modify: `apps/control-plane/frontend/src/components/TaskEventDiagnostics.test.tsx`
- Modify: `apps/control-plane/frontend/src/i18n.tsx`

- [ ] **Step 1: Write failing component tests**

Render with `selectedKinds` and `onSelectedKindsChange`, open the `Event type` button, toggle `Failed` and `Restart task`, and assert the callbacks, selected-count label, clear action, and Chinese group labels.

```typescript
fireEvent.click(screen.getByRole('button', { name: 'Event type: All' }));
fireEvent.click(screen.getByRole('checkbox', { name: 'Failed' }));
expect(onSelectedKindsChange).toHaveBeenCalledWith(['failed']);
```

- [ ] **Step 2: Verify component tests fail**

Run:

```bash
npm test -- --run src/components/TaskEventDiagnostics.test.tsx
```

Expected: the event type trigger and checkboxes do not exist.

- [ ] **Step 3: Implement the accessible grouped popover**

Add props `selectedKinds: readonly TaskEventHistoryKind[]` and `onSelectedKindsChange`. Render a `ListFilter` trigger next to `LookbackControl`, use `useDismissibleMenu` for outside-click and Escape handling, group constants by runtime and command source, and use checkbox inputs so multiple values remain selected while the menu stays open. The trigger label is `Event type: All` for an empty selection and `Event type: {count}` otherwise. Clear calls `onSelectedKindsChange([])`.

- [ ] **Step 4: Add localized labels**

Add English and Simplified Chinese keys for event type, all, selected count, runtime events, control commands, and clear filter. Reuse the existing `event.*` labels for individual kinds.

- [ ] **Step 5: Verify component tests pass**

Run the focused Vitest command from Step 2. Expected: all diagnostic tests pass.

- [ ] **Step 6: Commit the component slice**

```bash
git add apps/control-plane/frontend/src/components/TaskEventDiagnostics.tsx apps/control-plane/frontend/src/components/TaskEventDiagnostics.test.tsx apps/control-plane/frontend/src/i18n.tsx
git commit -m "feat: add task event type picker"
```

### Task 4: Wire State, Validate, And Restart The Plane

**Files:**
- Modify: `apps/control-plane/frontend/src/App.tsx`
- Test: `apps/control-plane/frontend/src/App.test.ts`

- [ ] **Step 1: Write a failing App behavior test**

Mock a task event request, select an event kind through the rendered diagnostic control, and assert the next request includes the kind with `offset=0`. This proves filtering resets pagination and triggers server fetching.

- [ ] **Step 2: Add App-owned filter state**

Add `taskEventKinds`, pass it into `loadTaskEventLogs` and `TaskEventDiagnostics`, and use a callback that updates kinds and resets the offset:

```typescript
const handleTaskEventKindsChange = useCallback((kinds: TaskEventHistoryKind[]) => {
  setTaskEventKinds(kinds);
  setTaskEventOffset(0);
}, []);
```

Include `taskEventKinds` in the fetch effect dependencies. Retain the selection when changing tasks, as specified.

- [ ] **Step 3: Run focused frontend and backend tests**

```bash
uv run pytest apps/control-plane/backend/tests/test_query_api.py -k task_events -v
cd apps/control-plane/frontend
npm test -- --run src/api.controlPlaneData.test.ts src/components/TaskEventDiagnostics.test.tsx src/App.test.ts
npm run build
```

Expected: pytest and Vitest pass, and Vite produces a successful production build.

- [ ] **Step 4: Rebuild and restart the baked control-plane image**

From `apps/control-plane`:

```bash
docker compose build plane
docker compose up -d plane
docker compose ps
```

Expected: `plane` and its dependency are running, and `plane` reaches `healthy` status.

- [ ] **Step 5: Commit the integration slice**

```bash
git add apps/control-plane/frontend/src/App.tsx apps/control-plane/frontend/src/App.test.ts
git commit -m "feat: connect task event kind filtering"
```

# Topology Flow Motion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a subtle staged data-flow animation to the task topology panel, with speed driven by current throughput.

**Architecture:** Keep the existing hand-built React/Tailwind topology component. Add small local helpers for active-state and speed derivation, render animated connector particles only for actively flowing tasks, and keep all motion CSS scoped to `TopologyFlow.tsx`.

**Tech Stack:** React 19, TypeScript, Tailwind utility classes, Vitest/jsdom, Testing Library.

---

## File Structure

- Modify: `frontend/src/components/TopologyFlow.tsx`
  - Adds flow-state helpers, scoped CSS keyframes, animated connector markup, and subtle node active classes.
- Create: `frontend/src/components/TopologyFlow.test.tsx`
  - Verifies helper behavior and rendered active/static topology states.

### Task 1: Add Focused Tests

**Files:**
- Create: `frontend/src/components/TopologyFlow.test.tsx`

- [ ] **Step 1: Create the test file**

Use this complete file:

```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { I18nProvider } from '../i18n';
import type { Task } from '../types';
import TopologyFlow, { getTopologyFlowDurationSeconds, isTopologyFlowActive } from './TopologyFlow';

const baseTask: Task = {
  id: 'svc:dev:produce_and_store',
  apiName: 'produce_and_store',
  apiServiceName: 'svc',
  environment: 'dev',
  serviceId: 'svc:dev',
  name: 'produce_and_store',
  viewStatus: 'running',
  supportedCommands: ['pause_task', 'restart_task'],
  pipelineSource: 'interval',
  pipelineSourceLabel: 'interval:5s',
  sourceKind: 'interval',
  sourceConfig: { seconds: 5 },
  sourceName: 'interval',
  pipelineSink: 'mysql_table_sink',
  pipelineSinkLabel: 'mysql.table_sink:cp_demo_events',
  sinkKind: 'mysql_table_sink',
  sinkConfig: { table: 'cp_demo_events', mode: 'upsert', keys: 'event_id' },
  sinkName: 'mysql.table_sink:cp_demo_events',
  concurrency: 1,
  retryAttempts: 3,
  uptimeReferenceAt: null,
  throughputPerMin: 120,
  successRate: 100,
  errorCount: 0,
  configYaml: '',
};

function renderTopology(overrides: Partial<Task> = {}) {
  const task = { ...baseTask, ...overrides };
  return render(
    <I18nProvider initialLocale="en">
      <TopologyFlow task={task} />
    </I18nProvider>,
  );
}

describe('topology flow motion', () => {
  it('activates flow only for running tasks with positive throughput', () => {
    expect(isTopologyFlowActive(baseTask)).toBe(true);
    expect(isTopologyFlowActive({ ...baseTask, viewStatus: 'paused' })).toBe(false);
    expect(isTopologyFlowActive({ ...baseTask, throughputPerMin: 0 })).toBe(false);
  });

  it('maps higher throughput to faster clamped durations', () => {
    expect(getTopologyFlowDurationSeconds(0)).toBe(2.4);
    expect(getTopologyFlowDurationSeconds(12)).toBeLessThan(getTopologyFlowDurationSeconds(1));
    expect(getTopologyFlowDurationSeconds(120)).toBe(0.75);
    expect(getTopologyFlowDurationSeconds(1000)).toBe(0.75);
  });

  it('renders active connectors for flowing tasks', () => {
    renderTopology({ throughputPerMin: 120 });

    const diagram = screen.getByTestId('topology-flow-diagram') as HTMLElement;
    expect(diagram.dataset.flowing).toBe('true');
    expect(diagram.style.getPropertyValue('--topology-flow-duration')).toBe('0.75s');
    expect(screen.getByTestId('topology-source-connector').dataset.flowing).toBe('true');
    expect(screen.getByTestId('topology-sink-connector').dataset.flowing).toBe('true');
    expect(screen.getAllByTestId('topology-flow-packet')).toHaveLength(4);
  });

  it('keeps connectors static for paused tasks', () => {
    renderTopology({ viewStatus: 'paused', throughputPerMin: 120 });

    const diagram = screen.getByTestId('topology-flow-diagram') as HTMLElement;
    expect(diagram.dataset.flowing).toBe('false');
    expect(screen.getByTestId('topology-source-connector').dataset.flowing).toBe('false');
    expect(screen.queryAllByTestId('topology-flow-packet')).toHaveLength(0);
  });
});
```

- [ ] **Step 2: Run the new test and verify it fails before implementation**

Run:

```bash
cd frontend && pnpm test -- src/components/TopologyFlow.test.tsx
```

Expected: fail because the exported helpers and test IDs do not exist yet.

### Task 2: Implement Staged Flow Motion

**Files:**
- Modify: `frontend/src/components/TopologyFlow.tsx`

- [ ] **Step 1: Add helper functions and scoped animation CSS**

Add exported helpers near the top of the file:

```tsx
const MIN_FLOW_DURATION_SECONDS = 0.75;
const MAX_FLOW_DURATION_SECONDS = 2.4;
const MAX_THROUGHPUT_FOR_SPEED = 120;

export function isTopologyFlowActive(task: Pick<Task, 'viewStatus' | 'throughputPerMin'>) {
  return task.viewStatus === 'running' && Number.isFinite(task.throughputPerMin) && task.throughputPerMin > 0;
}

export function getTopologyFlowDurationSeconds(throughputPerMin: number) {
  if (!Number.isFinite(throughputPerMin) || throughputPerMin <= 0) return MAX_FLOW_DURATION_SECONDS;
  const normalized = Math.min(1, Math.log10(Math.min(throughputPerMin, MAX_THROUGHPUT_FOR_SPEED) + 1) / Math.log10(MAX_THROUGHPUT_FOR_SPEED + 1));
  return Number((MAX_FLOW_DURATION_SECONDS - normalized * (MAX_FLOW_DURATION_SECONDS - MIN_FLOW_DURATION_SECONDS)).toFixed(2));
}
```

Add a `topologyFlowStyles` string with component-scoped classes for connector packets, sweep, reduced motion, and node glow. The handler animation must use `scale(1.015)` and a low-opacity 5px glow.

- [ ] **Step 2: Add an animated connector helper component**

Create a local `TopologyConnector` component in the same file. It should keep the existing responsive line dimensions, render `ArrowRight`, and render two particle spans only when `isFlowing` is true.

- [ ] **Step 3: Wire active state into the diagram**

In `TopologyFlow`, derive:

```tsx
const isFlowing = isTopologyFlowActive(task);
const flowDurationSeconds = getTopologyFlowDurationSeconds(task.throughputPerMin);
const topologyStyle = {
  '--topology-flow-duration': `${flowDurationSeconds}s`,
  '--topology-stage-duration': `${Math.max(1.6, flowDurationSeconds * 1.8).toFixed(2)}s`,
} as TopologyFlowStyle;
```

Apply `data-testid="topology-flow-diagram"`, `data-flowing`, and `style={topologyStyle}` to the diagram wrapper. Add active classes to source/task/sink node boxes only when `isFlowing` is true.

- [ ] **Step 4: Replace both static connectors**

Replace the two existing connecting-line blocks with:

```tsx
<TopologyConnector isFlowing={isFlowing} testId="topology-source-connector" />
<TopologyConnector isFlowing={isFlowing} testId="topology-sink-connector" />
```

### Task 3: Verify And Commit

**Files:**
- Test: `frontend/src/components/TopologyFlow.test.tsx`
- Test: `frontend/src/components/TopologyFlow.tsx`

- [ ] **Step 1: Run the focused test**

Run:

```bash
cd frontend && pnpm test -- src/components/TopologyFlow.test.tsx
```

Expected: all tests pass.

- [ ] **Step 2: Run the frontend build**

Run:

```bash
cd frontend && pnpm build
```

Expected: TypeScript and Vite build pass.

- [ ] **Step 3: Commit implementation**

Run:

```bash
git add frontend/src/components/TopologyFlow.tsx frontend/src/components/TopologyFlow.test.tsx
git commit -m "feat: animate topology data flow"
```

Expected: implementation commit contains only the topology component and its test.

### Task 4: Refresh Local Control Plane Container

**Files:**
- No source edits.

- [ ] **Step 1: Rebuild the plane image**

Run:

```bash
docker compose build plane
```

Expected: the `plane` image rebuilds successfully with the new frontend assets.

- [ ] **Step 2: Restart the plane container from the rebuilt image**

Run:

```bash
docker compose up -d plane
```

Expected: the `plane` service starts from the rebuilt image.

- [ ] **Step 3: Verify container health**

Run:

```bash
docker compose ps
```

Expected: the `plane` service is running and healthy, or the command output clearly shows why local Docker health is unavailable.

## Self-Review

- Spec coverage: The plan implements staged flow, throughput speed mapping, inactive states, subtle handler pulse, reduced-motion CSS, and focused verification.
- Red-flag scan: No draft markers remain; code-level steps identify exact helpers, files, and commands.
- Type consistency: Helper names, test IDs, and task fields match the TypeScript `Task` model and planned component edits.

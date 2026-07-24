# Plane Global Interaction System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a dependency-free, accessible interaction foundation for the onestep plane that makes loading, navigation, menus, dialogs, and operational actions feel immediate and unambiguous.

**Architecture:** Add a small set of global CSS motion primitives, keep asynchronous state in its current React owners, and extract only the toast behavior into a focused component. Apply the primitives to existing pages and controls without changing data contracts, routing semantics, or information architecture.

**Tech Stack:** React 19, TypeScript 5.9, Tailwind CSS 4, Lucide React, Vitest/jsdom, Testing Library, Playwright, Vite, Docker Compose.

---

## File Structure

- Modify: `frontend/src/index.css`
  - Defines timing/easing tokens, semantic interaction classes, skeletons, progress transforms, and reduced-motion behavior.
- Create: `frontend/src/components/ToastViewport.tsx`
  - Owns toast semantics, four-second dismissal, and manual close controls.
- Create: `frontend/src/components/ToastViewport.test.tsx`
  - Verifies status/alert roles and automatic/manual dismissal.
- Create: `frontend/src/components/useDismissibleMenu.ts`
  - Coordinates focus, outside-click dismissal, and `Escape` behavior for the three existing menu surfaces.
- Create: `frontend/src/components/useDismissibleMenu.test.tsx`
  - Verifies focus entry, outside-click dismissal, `Escape`, and focus restoration.
- Modify: `frontend/src/App.tsx`
  - Adds initial skeletons, background-refresh acknowledgment, stable action states, menu/dialog focus behavior, and the toast viewport.
- Modify: `frontend/src/App.test.ts`
  - Keeps existing status-helper coverage and adds focused initial/background-loading helper coverage.
- Modify: `frontend/src/i18n.tsx`
  - Adds accessible loading and notification-dismiss labels in English and Chinese.
- Modify: `frontend/src/components/Sidebar.tsx`
  - Applies consistent press and focus feedback.
- Modify: `frontend/src/components/ServicesList.tsx`
  - Applies restrained row feedback without lift or decorative motion.
- Modify: `frontend/src/components/TasksList.tsx`
  - Adds dismissible-menu behavior, stable pending feedback, and meaningful runtime status motion.
- Modify: `frontend/src/components/TasksList.test.tsx`
  - Verifies local pending scope and task-menu keyboard dismissal.
- Modify: `frontend/src/components/LocaleSwitcher.tsx`
  - Adds dismissible-menu focus behavior.
- Modify: `frontend/src/components/NotificationSettingsPage.tsx`
  - Aligns page, save/test/refresh controls, popovers, and loading semantics.
- Modify: `frontend/src/components/NotificationSettingsPage.test.tsx`
  - Verifies busy-state semantics during a deferred refresh.
- Modify: `frontend/src/components/ResourceChart.tsx`
  - Aligns loading, empty, error, and progress transitions.
- Modify: `frontend/src/components/ResourceChart.test.tsx`
  - Verifies loading uses an accessible non-blocking status layer.
- Modify: `frontend/src/components/TaskEventDiagnostics.tsx`
  - Aligns loading, error, empty, disclosure, and paging feedback.
- Modify: `frontend/src/components/TaskEventDiagnostics.test.tsx`
  - Verifies accessible loading and disabled paging semantics.
- Modify: `frontend/src/components/TopologyFlow.tsx`
  - Reuses global easing variables while preserving the existing staged flow behavior.
- Modify: `frontend/e2e/app.spec.ts`
  - Covers initial skeletons, content-preserving refresh, dialog focus, menu dismissal, and reduced motion.

Do not modify backend code, API schemas, WebSocket handling, reporter payloads, task lifecycle semantics, or remote-control behavior.

## Dirty Worktree Rule

`frontend/src/App.tsx`, `frontend/src/App.test.ts`,
`frontend/src/components/ServicesList.tsx`,
`frontend/src/components/ServicesList.test.tsx`, and `frontend/e2e/app.spec.ts`
already contain user changes. Read their current contents before each edit and
preserve those changes. Do not stage an overlapping dirty file with a whole-file
`git add`. Implementation commits are optional until the owner approves the
combined diff; verification is mandatory.

---

### Task 1: Global Interaction Foundation

**Files:**
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Add the approved tokens and semantic classes**

Append the following block after the scrollbar rules:

```css
:root {
  --motion-micro: 120ms;
  --motion-small: 220ms;
  --motion-medium: 320ms;
  --ease-enter: cubic-bezier(0.16, 1, 0.3, 1);
  --ease-exit: cubic-bezier(0.55, 0, 1, 0.45);
  --ease-move: cubic-bezier(0.65, 0, 0.35, 1);
}

.ui-pressable {
  transition:
    color var(--motion-micro) var(--ease-enter),
    background-color var(--motion-micro) var(--ease-enter),
    border-color var(--motion-micro) var(--ease-enter),
    box-shadow var(--motion-micro) var(--ease-enter),
    transform var(--motion-micro) var(--ease-enter),
    opacity var(--motion-micro) var(--ease-enter);
}

.ui-pressable:active:not(:disabled) {
  transform: translateY(1px);
}

.ui-page-enter {
  animation: ui-page-enter var(--motion-small) var(--ease-enter) both;
}

.ui-popover-enter {
  transform-origin: top right;
  animation: ui-popover-enter var(--motion-small) var(--ease-enter) both;
}

.ui-dialog-backdrop {
  animation: ui-fade-in var(--motion-small) var(--ease-enter) both;
}

.ui-dialog-enter {
  animation: ui-dialog-enter var(--motion-medium) var(--ease-enter) both;
}

.ui-toast-enter {
  animation: ui-toast-enter var(--motion-small) var(--ease-enter) both;
}

.ui-panel-state-enter {
  animation: ui-fade-in var(--motion-small) var(--ease-enter) both;
}

.ui-skeleton {
  position: relative;
  overflow: hidden;
  background: #e9eef5;
}

.ui-skeleton::after {
  position: absolute;
  inset: 0;
  content: "";
  background: linear-gradient(90deg, transparent, rgb(255 255 255 / 0.72), transparent);
  transform: translateX(-100%);
  animation: ui-skeleton-sweep 1.6s var(--ease-move) infinite;
}

.ui-progress-fill {
  transform: scaleX(var(--ui-progress, 0));
  transform-origin: left center;
  transition: transform var(--motion-medium) var(--ease-enter);
}

.ui-refresh-ack {
  position: absolute;
  inset: 0;
  pointer-events: none;
  border-radius: 8px;
  background: rgb(79 70 229 / 0.055);
  animation: ui-refresh-ack 560ms var(--ease-enter) both;
}

@keyframes ui-page-enter {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes ui-popover-enter {
  from { opacity: 0; transform: translateY(-4px) scale(0.99); }
  to { opacity: 1; transform: translateY(0) scale(1); }
}

@keyframes ui-dialog-enter {
  from { opacity: 0; transform: scale(0.985); }
  to { opacity: 1; transform: scale(1); }
}

@keyframes ui-toast-enter {
  from { opacity: 0; transform: translateY(6px); }
  to { opacity: 1; transform: translateY(0); }
}

@keyframes ui-fade-in {
  from { opacity: 0; }
  to { opacity: 1; }
}

@keyframes ui-skeleton-sweep {
  to { transform: translateX(100%); }
}

@keyframes ui-refresh-ack {
  0% { opacity: 0; }
  22% { opacity: 1; }
  100% { opacity: 0; }
}

@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
  }
}
```

- [ ] **Step 2: Build to catch Tailwind/CSS parsing errors**

Run:

```bash
cd frontend && pnpm build
```

Expected: TypeScript checks pass and Vite emits `dist/assets/*.css` without CSS parser errors.

- [ ] **Step 3: Commit only the clean CSS foundation**

Run:

```bash
git add frontend/src/index.css
git diff --cached --check
git commit -m "feat: add plane interaction foundation"
```

Expected: the commit contains only `frontend/src/index.css`.

---

### Task 2: Accessible Toast Lifecycle

**Files:**
- Create: `frontend/src/components/ToastViewport.tsx`
- Create: `frontend/src/components/ToastViewport.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/i18n.tsx`

- [ ] **Step 1: Write failing toast tests**

Create `frontend/src/components/ToastViewport.test.tsx`:

```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import ToastViewport, { type ToastMessage } from './ToastViewport';

const successToast: ToastMessage = { id: 'success', message: 'Saved', type: 'success' };
const warningToast: ToastMessage = { id: 'warning', message: 'Request failed', type: 'warn' };

afterEach(() => {
  vi.useRealTimers();
});

describe('ToastViewport', () => {
  it('uses polite status semantics for success and alert semantics for warnings', () => {
    render(
      <ToastViewport
        dismissLabel="Dismiss notification"
        onDismiss={() => undefined}
        toasts={[successToast, warningToast]}
      />,
    );

    expect(screen.getByRole('status').textContent).toContain('Saved');
    expect(screen.getByRole('alert').textContent).toContain('Request failed');
  });

  it('dismisses a toast from its close button', () => {
    const onDismiss = vi.fn();
    render(
      <ToastViewport
        dismissLabel="Dismiss notification"
        onDismiss={onDismiss}
        toasts={[successToast]}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: 'Dismiss notification' }));
    expect(onDismiss).toHaveBeenCalledWith('success');
  });

  it('automatically dismisses a toast after four seconds', () => {
    vi.useFakeTimers();
    const onDismiss = vi.fn();
    render(
      <ToastViewport
        dismissLabel="Dismiss notification"
        onDismiss={onDismiss}
        toasts={[successToast]}
      />,
    );

    vi.advanceTimersByTime(3999);
    expect(onDismiss).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onDismiss).toHaveBeenCalledWith('success');
  });
});
```

- [ ] **Step 2: Run the toast test and verify it fails**

Run:

```bash
cd frontend && pnpm test -- src/components/ToastViewport.test.tsx
```

Expected: fail because `ToastViewport.tsx` does not exist.

- [ ] **Step 3: Implement the toast viewport**

Create `frontend/src/components/ToastViewport.tsx`:

```tsx
import { useEffect } from 'react';
import { X } from 'lucide-react';

export type ToastType = 'success' | 'info' | 'warn';

export interface ToastMessage {
  id: string;
  message: string;
  type: ToastType;
}

interface ToastViewportProps {
  dismissLabel: string;
  onDismiss: (id: string) => void;
  toasts: ToastMessage[];
}

interface ToastItemProps {
  dismissLabel: string;
  onDismiss: (id: string) => void;
  toast: ToastMessage;
}

function ToastItem({ dismissLabel, onDismiss, toast }: ToastItemProps) {
  useEffect(() => {
    const timeoutId = window.setTimeout(() => onDismiss(toast.id), 4000);
    return () => window.clearTimeout(timeoutId);
  }, [onDismiss, toast.id]);

  const palette =
    toast.type === 'info'
      ? 'border-slate-700/60 bg-[#1a1c24] text-white'
      : toast.type === 'warn'
        ? 'border-rose-200 bg-rose-50 text-rose-900'
        : 'border-slate-200 bg-white text-slate-800';
  const indicator = toast.type === 'warn' ? 'bg-rose-500' : toast.type === 'success' ? 'bg-emerald-500' : 'bg-indigo-600';

  return (
    <div
      aria-atomic="true"
      className={`ui-toast-enter flex items-center gap-3 rounded-lg border p-3.5 text-xs font-medium shadow-lg ${palette}`}
      role={toast.type === 'warn' ? 'alert' : 'status'}
    >
      <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${indicator}`} />
      <span className="min-w-0 flex-1 break-words font-semibold leading-relaxed">{toast.message}</span>
      <button
        aria-label={dismissLabel}
        className="ui-pressable grid h-7 w-7 shrink-0 place-items-center rounded-md text-current opacity-55 hover:bg-black/5 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        onClick={() => onDismiss(toast.id)}
        type="button"
      >
        <X aria-hidden="true" className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export default function ToastViewport({ dismissLabel, onDismiss, toasts }: ToastViewportProps) {
  return (
    <div className="fixed inset-x-4 bottom-4 z-50 ml-auto max-w-sm space-y-2.5 sm:inset-x-auto sm:bottom-6 sm:right-6 sm:w-full">
      {toasts.map((toast) => (
        <ToastItem
          dismissLabel={dismissLabel}
          key={toast.id}
          onDismiss={onDismiss}
          toast={toast}
        />
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Integrate the viewport and remove duplicate timers**

In `App.tsx`, import `ToastViewport`, `ToastMessage`, and `ToastType`. Change the
toast state and helper to:

```tsx
const [toasts, setToasts] = useState<ToastMessage[]>([]);

const dismissToast = useCallback((id: string) => {
  setToasts((current) => current.filter((toast) => toast.id !== id));
}, []);

const addToast = (message: string, type: ToastType = 'success') => {
  const id = Math.random().toString(36).slice(2, 11);
  setToasts((current) => [...current, { id, message, type }]);
};
```

Replace the existing toast map with:

```tsx
<ToastViewport
  dismissLabel={tr('toast.dismiss')}
  onDismiss={dismissToast}
  toasts={toasts}
/>
```

Add both locale entries in `i18n.tsx`:

```tsx
'toast.dismiss': 'Dismiss notification',
```

```tsx
'toast.dismiss': '关闭通知',
```

- [ ] **Step 5: Run focused tests and build**

Run:

```bash
cd frontend && pnpm test -- src/components/ToastViewport.test.tsx src/App.test.ts
cd frontend && pnpm build
```

Expected: toast tests, existing App helper tests, TypeScript, and Vite build pass.

---

### Task 3: Initial Loading, Background Refresh, And Page Continuity

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.ts`
- Modify: `frontend/src/components/OverviewPage.tsx`
- Modify: `frontend/src/components/ServicesList.tsx`
- Modify: `frontend/src/components/NotificationSettingsPage.tsx`

- [ ] **Step 1: Add and test a pure loading-mode helper**

Add these assertions to `frontend/src/App.test.ts`:

```tsx
import { getControlPlaneLoadingMode } from './App';

it('uses a skeleton only when the initial API request has no content', () => {
  expect(getControlPlaneLoadingMode(true, 0)).toBe('initial');
  expect(getControlPlaneLoadingMode(true, 2)).toBe('refresh');
  expect(getControlPlaneLoadingMode(false, 0)).toBe('settled');
});
```

Run:

```bash
cd frontend && pnpm test -- src/App.test.ts
```

Expected: fail because `getControlPlaneLoadingMode` is not exported.

- [ ] **Step 2: Implement the helper and local skeleton**

Add near the existing exported task helpers in `App.tsx`:

```tsx
export function getControlPlaneLoadingMode(isLoading: boolean, serviceCount: number) {
  if (isLoading && serviceCount === 0) return 'initial' as const;
  if (isLoading) return 'refresh' as const;
  return 'settled' as const;
}

function ControlPlaneSkeleton({ label }: { label: string }) {
  return (
    <div
      aria-label={label}
      className="mx-auto max-w-7xl space-y-6"
      data-testid="control-plane-skeleton"
      role="status"
    >
      <h2 className="sr-only">{label}</h2>
      <div aria-hidden="true" className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[0, 1, 2, 3].map((item) => (
          <div className="h-24 rounded-lg border border-slate-200 bg-white p-4" key={item}>
            <div className="ui-skeleton h-3 w-20 rounded" />
            <div className="ui-skeleton mt-5 h-7 w-14 rounded" />
          </div>
        ))}
      </div>
      <div aria-hidden="true" className="overflow-hidden rounded-lg border border-slate-200 bg-white">
        <div className="border-b border-slate-200 p-4"><div className="ui-skeleton h-8 w-64 max-w-full rounded" /></div>
        {[0, 1, 2, 3].map((item) => (
          <div className="grid grid-cols-[2fr_1fr_1fr] gap-4 border-b border-slate-100 p-4 last:border-0" key={item}>
            <div className="ui-skeleton h-4 rounded" />
            <div className="ui-skeleton h-4 rounded" />
            <div className="ui-skeleton h-4 rounded" />
          </div>
        ))}
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Separate initial and background rendering**

Derive the loading mode and refresh acknowledgment state:

```tsx
const loadingMode = getControlPlaneLoadingMode(isLoadingApi, services.length);
const [refreshFeedbackKey, setRefreshFeedbackKey] = useState(0);
```

After a non-silent refresh succeeds, increment `refreshFeedbackKey` before
adding the success toast. Give the refresh button stable dimensions and semantics:

```tsx
<button
  aria-busy={loadingMode === 'refresh'}
  className="ui-pressable flex min-w-[92px] items-center justify-center gap-1 rounded-md border border-slate-200 bg-slate-50 px-2 py-1 text-[11px] font-bold text-slate-600 hover:bg-slate-100 disabled:cursor-wait disabled:opacity-60"
  disabled={isLoadingApi}
  onClick={() => void refreshControlPlaneData(selectedServiceId)}
  type="button"
>
```

Replace the empty centered spinner branch with:

```tsx
{loadingMode === 'initial' ? (
  <ControlPlaneSkeleton label={tr('api.connecting')} />
) : services.length === 0 ? (
  <div className="mx-auto max-w-7xl rounded-lg border border-slate-200 bg-white p-8 text-center shadow-xs">
    <h2 className="text-sm font-bold text-slate-800">{tr('api.noServices')}</h2>
    <p className="mt-1 text-xs font-medium text-slate-500">
      {apiError ? tr('api.noServicesHint', { error: apiError }) : tr('api.waitingForServices')}
    </p>
  </div>
) : null}
```

Immediately after this empty-state branch, wrap the existing four conditional
view blocks, beginning with `{currentView === 'overview' && (` and ending with
the closing service-detail condition, with these exact opening and closing tags:

```tsx
{services.length > 0 && (
  <div className="relative">
    {refreshFeedbackKey > 0 && <span className="ui-refresh-ack" key={refreshFeedbackKey} />}
    <div
      className="ui-page-enter"
      key={`${currentView}:${selectedServiceId}:${activeTab}:${selectedTaskId ?? ''}`}
    >
```

```tsx
    </div>
  </div>
)}
```

Move no JSX inside the existing view blocks other than adding this wrapper.

- [ ] **Step 4: Remove duplicate page-entry classes**

Remove `animate-fadeIn` from the top-level wrappers in `OverviewPage.tsx`,
`ServicesList.tsx`, `NotificationSettingsPage.tsx`, and the service/task detail
wrappers in `App.tsx`. The keyed `ui-page-enter` stage is now the single owner of
page entry.

- [ ] **Step 5: Run focused tests and build**

Run:

```bash
cd frontend && pnpm test -- src/App.test.ts src/components/ServicesList.test.tsx src/components/NotificationSettingsPage.test.tsx
cd frontend && pnpm build
```

Expected: all selected tests and the frontend build pass without changing route,
selection, or existing service-label behavior.

---

### Task 4: Keyboard-Complete Menus And Manual-Run Dialog

**Files:**
- Create: `frontend/src/components/useDismissibleMenu.ts`
- Create: `frontend/src/components/useDismissibleMenu.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/LocaleSwitcher.tsx`
- Modify: `frontend/src/components/TasksList.tsx`
- Modify: `frontend/src/components/TasksList.test.tsx`

- [ ] **Step 1: Write failing hook tests**

Create `frontend/src/components/useDismissibleMenu.test.tsx`:

```tsx
import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import useDismissibleMenu from './useDismissibleMenu';

function MenuHarness({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(false);
  const { menuRef, triggerRef } = useDismissibleMenu({
    onClose: () => { setOpen(false); onClose(); },
    open,
  });
  return (
    <div>
      <button onClick={() => setOpen(true)} ref={triggerRef}>Open menu</button>
      {open && <div ref={menuRef}><button>First action</button></div>}
      <button>Outside</button>
    </div>
  );
}

describe('useDismissibleMenu', () => {
  it('focuses the first item, closes on Escape, and restores trigger focus', () => {
    const onClose = vi.fn();
    render(<MenuHarness onClose={onClose} />);

    const trigger = screen.getByRole('button', { name: 'Open menu' });
    fireEvent.click(trigger);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'First action' }));

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'First action' })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });

  it('closes when a pointer starts outside the trigger and menu', () => {
    const onClose = vi.fn();
    render(<MenuHarness onClose={onClose} />);

    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }));
    fireEvent.pointerDown(screen.getByRole('button', { name: 'Outside' }));

    expect(onClose).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('button', { name: 'First action' })).toBeNull();
  });
});
```

Run:

```bash
cd frontend && pnpm test -- src/components/useDismissibleMenu.test.tsx
```

Expected: fail because the hook does not exist.

- [ ] **Step 2: Implement the dismissible-menu hook**

Create `frontend/src/components/useDismissibleMenu.ts`:

```tsx
import { useCallback, useEffect, useRef } from 'react';

interface DismissibleMenuOptions {
  onClose: () => void;
  open: boolean;
}

export default function useDismissibleMenu({ onClose, open }: DismissibleMenuOptions) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuElementRef = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;
  const menuRef = useCallback((node: HTMLElement | null) => {
    menuElementRef.current = node;
  }, []);

  useEffect(() => {
    if (!open) return;

    const firstItem = menuElementRef.current?.querySelector<HTMLElement>(
      '[role="option"], [role="menuitem"], button:not(:disabled)',
    );
    firstItem?.focus();

    const closeAndRestoreFocus = () => {
      onCloseRef.current();
      triggerRef.current?.focus();
    };
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        closeAndRestoreFocus();
      }
    };
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target as Node;
      if (!menuElementRef.current?.contains(target) && !triggerRef.current?.contains(target)) {
        onCloseRef.current();
      }
    };

    document.addEventListener('keydown', handleKeyDown);
    document.addEventListener('pointerdown', handlePointerDown);
    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.removeEventListener('pointerdown', handlePointerDown);
    };
  }, [open]);

  return { menuRef, triggerRef };
}
```

- [ ] **Step 3: Apply the hook to all three menus**

Use one hook instance in the environment menu, one in `LocaleSwitcher`, and one
in `TasksList` for the currently open task. Attach `triggerRef` and `menuRef`,
remove the full-screen click-catcher elements, and add `ui-popover-enter` to each
menu panel. Preserve listbox/option roles for environment and locale selectors.
Give the task menu `role="menu"` and its action buttons `role="menuitem"`.

In `TasksList.test.tsx`, add a two-task case with `pendingTaskId` set to the first
task. Assert that only the first task menu exposes `Processing...`, while the
second task menu still exposes enabled task actions. Add an `Escape` assertion
that the open menu disappears and the More Actions trigger regains focus.

Use these locale entries for the trigger label:

```tsx
'button.moreActions': 'More actions for {name}',
```

```tsx
'button.moreActions': '{name} 的更多操作',
```

Add this test with a `renderTasks` helper that renders the same `I18nProvider`
and `TasksList` props as the existing `renderTasksList` helper:

```tsx
it('keeps pending actions local and restores focus after Escape', () => {
  const first = {
    ...baseTask,
    id: 'service:prod:first',
    name: 'first',
    viewStatus: 'running' as const,
    supportedCommands: ['pause_task', 'restart_task'] as TaskCommandKind[],
  };
  const second = {
    ...first,
    id: 'service:prod:second',
    name: 'second',
  };
  renderTasks([first, second], 'service:prod:first');

  const triggers = screen.getAllByRole('button', { name: /More actions for/ });
  fireEvent.click(triggers[0]);
  expect(screen.getAllByText('Processing...').length).toBeGreaterThan(0);
  fireEvent.keyDown(document, { key: 'Escape' });
  expect(document.activeElement).toBe(triggers[0]);

  fireEvent.click(triggers[1]);
  expect(screen.queryByText('Processing...')).toBeNull();
  const pause = screen.getByRole('menuitem', { name: 'Pause' }) as HTMLButtonElement;
  expect(pause.disabled).toBe(false);
});
```

- [ ] **Step 4: Add focus management to the manual-run dialog**

In `App.tsx`, add refs for the dialog and return-focus element. Capture
`document.activeElement` before opening. While `manualRunTask` is non-null:

- focus the payload textarea marked with `data-autofocus="true"`;
- close on `Escape` when submission is not pending;
- cycle `Tab`/`Shift+Tab` within enabled buttons, inputs, and textareas;
- restore focus after `closeManualRunDialog` clears state.

Attach `ui-dialog-backdrop` to the backdrop, `ui-dialog-enter` and the dialog ref
to the panel, and `aria-busy={isManualRunSubmitting}` to the dialog. Keep the
existing submit/cancel rules and API calls unchanged.

- [ ] **Step 5: Run focused tests and build**

Run:

```bash
cd frontend && pnpm test -- src/components/useDismissibleMenu.test.tsx src/components/TasksList.test.tsx src/App.test.ts
cd frontend && pnpm build
```

Expected: menu, task, and App tests pass; TypeScript accepts the generic element
refs; no existing task command is enabled or disabled differently.

---

### Task 5: Apply Operational Feedback Across Existing Surfaces

**Files:**
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/components/Sidebar.tsx`
- Modify: `frontend/src/components/ServicesList.tsx`
- Modify: `frontend/src/components/TasksList.tsx`
- Modify: `frontend/src/components/NotificationSettingsPage.tsx`
- Modify: `frontend/src/components/NotificationSettingsPage.test.tsx`
- Modify: `frontend/src/components/ResourceChart.tsx`
- Modify: `frontend/src/components/ResourceChart.test.tsx`
- Modify: `frontend/src/components/TaskEventDiagnostics.tsx`
- Modify: `frontend/src/components/TaskEventDiagnostics.test.tsx`
- Modify: `frontend/src/components/TopologyFlow.tsx`

- [ ] **Step 1: Add busy-state tests before class changes**

Add a `ResourceChart` test that renders `isLoading` and asserts a `role="status"`
element named by `chart.loadingMetrics`. Add a `TaskEventDiagnostics` test that
renders `isLoading` and asserts its loading row has `role="status"`. Add a
notification settings test using a deferred fetch promise and assert the Refresh
button has `aria-busy="true"` while the promise is pending.

Use these exact assertions in the three existing test files, adding `screen` to
the ResourceChart Testing Library import:

```tsx
it('announces metric loading without changing the chart frame', () => {
  render(
    <I18nProvider initialLocale="en">
      <ResourceChart
        error={null}
        isLoading
        lookbackMinutes={15}
        onLookbackMinutesChange={vi.fn()}
        windows={[]}
      />
    </I18nProvider>,
  );

  expect(screen.getByRole('status').textContent).toContain('Loading task metrics...');
});
```

```tsx
it('announces task event loading', () => {
  render(
    <I18nProvider initialLocale="en">
      <TaskEventDiagnostics
        error={null}
        isLoading
        limit={20}
        logs={[]}
        lookbackMinutes={15}
        offset={0}
        onLookbackMinutesChange={vi.fn()}
        onPageChange={vi.fn()}
        total={0}
      />
    </I18nProvider>,
  );

  expect(screen.getByRole('status').textContent).toContain('Loading task events...');
});
```

```tsx
it('marks notification refresh as busy while requests are pending', async () => {
  const pending = new Promise<Response>(() => undefined);
  vi.spyOn(window, 'fetch').mockReturnValue(pending);
  renderPage();

  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Refreshing' }).getAttribute('aria-busy')).toBe('true');
  });
});
```

Run:

```bash
cd frontend && pnpm test -- src/components/ResourceChart.test.tsx src/components/TaskEventDiagnostics.test.tsx src/components/NotificationSettingsPage.test.tsx
```

Expected: at least the busy/status assertions fail before implementation.

- [ ] **Step 2: Standardize control feedback**

Apply `ui-pressable` to buttons and interactive rows touched by this plan. Add
stable `min-width` only where a label changes while pending. Add `aria-busy` to:

- global refresh;
- restart all;
- deploy update;
- selected-task restart/pause/manual run;
- task-card pending actions;
- notification refresh, test, create/save, and delete confirmation actions.

Do not disable navigation during these actions. Preserve every existing command
prerequisite and `disabled` expression.

- [ ] **Step 3: Standardize panel state transitions**

In `ResourceChart` and `TaskEventDiagnostics`, add `role="status"`,
`aria-live="polite"`, and `ui-panel-state-enter` to loading and empty states. Use
`role="alert"` for error layers. Keep the chart and diagnostics containers at
their current fixed dimensions so state changes do not move surrounding content.

In `NotificationSettingsPage`, use `ui-page-enter` only when it is rendered
outside the keyed App stage; otherwise remove its duplicate page animation. Add
`ui-popover-enter` to variable menus and `ui-panel-state-enter` to inline
create/edit/delete state changes.

- [ ] **Step 4: Remove decorative continuous motion**

Remove `animate-pulse` from task status dots, the restart-all panel, and static
telemetry warning dots. Keep spinner animation for pending actions and keep the
existing topology packet/node motion only when `isTopologyFlowActive(task)` is
true. In `TopologyFlow.tsx`, replace hard-coded enter easing with
`var(--ease-enter)` where the animation meaning remains unchanged.

- [ ] **Step 5: Convert deployment progress to transform**

Replace the width-based fill with:

```tsx
// Add `type CSSProperties` to the existing React import in App.tsx.
<div
  aria-hidden="true"
  className="ui-progress-fill h-full rounded-full bg-indigo-600"
  style={{ '--ui-progress': deploymentProgress / 100 } as CSSProperties}
/>
```

Keep the numeric percentage as visible text and add `aria-valuemin={0}`,
`aria-valuemax={100}`, and `aria-valuenow={deploymentProgress}` to the progress
container with `role="progressbar"`.

- [ ] **Step 6: Run the complete unit suite and build**

Run:

```bash
cd frontend && pnpm test -- --run
cd frontend && pnpm build
```

Expected: all component/App tests pass and the production build completes.

---

### Task 6: End-To-End, Reduced-Motion, Container, And Visual Verification

**Files:**
- Modify: `frontend/e2e/app.spec.ts`
- No backend source edits.

- [ ] **Step 1: Extend the existing initial-load test**

In the existing `does not render demo services while the initial API load is
pending` test, assert that `control-plane-skeleton` is visible while the deferred
services request is pending, then resolve the request and assert the skeleton is
removed while API-backed service content appears.

- [ ] **Step 2: Add content-preserving refresh coverage**

Add a test that loads the billing service, defers the next services-list request,
clicks Refresh, and verifies:

```tsx
await expect(page.getByRole('button', { name: 'Refreshing' })).toHaveAttribute('aria-busy', 'true');
await expect(page.getByText('billing-sync')).toBeVisible();
await expect(page.getByTestId('control-plane-skeleton')).toHaveCount(0);
```

Resolve the request and assert the success toast appears.

- [ ] **Step 3: Add keyboard and reduced-motion coverage**

Add one test that opens the environment menu, presses `Escape`, and verifies focus
returns to its trigger. Open the manual-run dialog from a task, verify the payload
textarea receives focus, press `Escape`, and verify focus returns to the Run once
button.

Add a separate test with:

```tsx
await page.emulateMedia({ reducedMotion: 'reduce' });
await installApiMocks(page);
await page.goto('/services');
const duration = await page.getByTestId('topology-flow-diagram').evaluate(
  (element) => getComputedStyle(element.querySelector('[data-testid="topology-flow-packet"]')!).animationDuration,
);
expect(duration).toBe('0.01ms');
```

- [ ] **Step 4: Run the end-to-end suite**

Run:

```bash
cd frontend && pnpm e2e
```

Expected: all Chromium Playwright tests pass.

- [ ] **Step 5: Rebuild and restart the plane image**

Run from `onestep-control-plane/`:

```bash
docker compose build plane
docker compose up -d plane
docker compose ps
```

Expected: the rebuilt `plane` service is running and reports healthy before UI
verification begins.

- [ ] **Step 6: Inspect desktop and narrow viewports**

Use the running plane at its compose-exposed URL. Capture and inspect screenshots
at `1440x900` and `390x844` for overview, services, task detail, notifications,
the manual-run dialog, a menu, and a toast. Verify no overlap, clipping, horizontal
overflow, unstable button dimensions, or blank loading regions. Repeat with
reduced motion enabled and confirm all controls remain understandable.

- [ ] **Step 7: Review the final diff without staging user changes**

Run:

```bash
git diff --check
git status --short
git diff --stat
```

Expected: no whitespace errors; the diff contains only the interaction-system
files plus the pre-existing user edits identified before implementation. Do not
commit overlapping dirty files until the owner approves the combined diff.

## Self-Review

- Spec coverage: Tasks 1-6 cover shared timing/easing, stable press feedback,
  initial skeletons, non-blocking refresh, keyed page entry, menus, dialog focus,
  toasts, progress transforms, local pending state, errors, reduced motion,
  desktop/mobile inspection, and the required plane rebuild.
- Draft-marker scan: The plan contains no draft markers or undefined follow-up
  work. Existing conditional JSX is preserved through exact wrapper insertion
  points rather than being restated or rewritten.
- Type consistency: `ToastMessage`, `ToastType`, `getControlPlaneLoadingMode`,
  `useDismissibleMenu`, CSS class names, and data-test IDs are named consistently
  in tests and implementation steps.
- Scope: All changes remain in the frontend. No protocol, persistence, runtime,
  or reporter coordination is required.

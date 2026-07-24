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

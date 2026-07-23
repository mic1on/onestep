import { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import useDismissibleMenu from './useDismissibleMenu';

function MenuHarness({ onClose, trapFocus = false }: { onClose: () => void; trapFocus?: boolean }) {
  const [open, setOpen] = useState(false);
  const { menuRef, triggerRef } = useDismissibleMenu({
    onClose: () => {
      setOpen(false);
      onClose();
    },
    open,
    trapFocus,
  });
  return (
    <div>
      <button onClick={() => setOpen(true)} ref={triggerRef}>
        Open menu
      </button>
      {open && (
        <div ref={menuRef}>
          <button>First action</button>
          <button>Last action</button>
        </div>
      )}
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

  it('loops Tab focus within an opted-in modal menu', () => {
    const onClose = vi.fn();
    render(<MenuHarness onClose={onClose} trapFocus />);

    fireEvent.click(screen.getByRole('button', { name: 'Open menu' }));
    const firstAction = screen.getByRole('button', { name: 'First action' });
    const lastAction = screen.getByRole('button', { name: 'Last action' });

    lastAction.focus();
    fireEvent.keyDown(lastAction, { key: 'Tab' });
    expect(document.activeElement).toBe(firstAction);

    fireEvent.keyDown(firstAction, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(lastAction);
  });
});

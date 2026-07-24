import { useCallback, useEffect, useRef } from 'react';

interface DismissibleMenuOptions {
  onClose: () => void;
  open: boolean;
  trapFocus?: boolean;
}

const focusableSelector = 'a[href], button:not(:disabled), [tabindex]:not([tabindex="-1"])';

export default function useDismissibleMenu({
  onClose,
  open,
  trapFocus = false,
}: DismissibleMenuOptions) {
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
      if (trapFocus && event.key === 'Tab') {
        const focusableItems = Array.from(
          menuElementRef.current?.querySelectorAll<HTMLElement>(focusableSelector) ?? [],
        );
        const firstFocusableItem = focusableItems[0];
        const lastFocusableItem = focusableItems.at(-1);
        if (!firstFocusableItem || !lastFocusableItem) return;

        if (event.shiftKey && document.activeElement === firstFocusableItem) {
          event.preventDefault();
          lastFocusableItem.focus();
        } else if (!event.shiftKey && document.activeElement === lastFocusableItem) {
          event.preventDefault();
          firstFocusableItem.focus();
        }
      }
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
  }, [open, trapFocus]);

  return { menuRef, triggerRef };
}

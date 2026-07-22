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

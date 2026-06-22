import type { ReactNode } from "react";

import { VibeButton } from "./VibeButton";

type VibePopconfirmProps = {
  body: ReactNode;
  cancelDisabled?: boolean;
  cancelLabel: ReactNode;
  children: ReactNode;
  className?: string;
  confirmDisabled?: boolean;
  confirmLabel: ReactNode;
  onCancel: () => void;
  onConfirm: () => void;
  open: boolean;
  title: ReactNode;
};

export function VibePopconfirm({
  body,
  cancelDisabled = false,
  cancelLabel,
  children,
  className,
  confirmDisabled = false,
  confirmLabel,
  onCancel,
  onConfirm,
  open,
  title,
}: VibePopconfirmProps) {
  return (
    <div className={["notification-delete-popconfirm", className].filter(Boolean).join(" ")}>
      {children}
      {open ? (
        <div className="notification-delete-bubble" role="alertdialog" aria-live="polite">
          <div className="notification-delete-bubble-arrow" aria-hidden="true" />
          <div className="notification-delete-bubble-copy">
            <strong>{title}</strong>
            <p>{body}</p>
          </div>
          <div className="notification-delete-bubble-actions">
            <VibeButton disabled={cancelDisabled} onClick={onCancel} variant="secondary">
              {cancelLabel}
            </VibeButton>
            <VibeButton disabled={confirmDisabled} onClick={onConfirm} variant="dangerSolid">
              {confirmLabel}
            </VibeButton>
          </div>
        </div>
      ) : null}
    </div>
  );
}

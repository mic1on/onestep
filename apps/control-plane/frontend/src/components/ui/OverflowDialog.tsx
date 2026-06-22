import { useEffect, useId, type MouseEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import { VibeInlineButton } from "./VibeInlineButton";

type OverflowDialogProps = {
  open: boolean;
  title: string;
  description?: string;
  className?: string;
  overlayClassName?: string;
  onClose: () => void;
  children: ReactNode;
};

export function OverflowDialog({
  open,
  title,
  description,
  className,
  overlayClassName,
  onClose,
  children,
}: OverflowDialogProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const descriptionId = useId();

  useEffect(() => {
    if (!open) {
      return undefined;
    }

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  if (!open) {
    return null;
  }

  function stopPropagation(event: MouseEvent<HTMLDivElement>) {
    event.stopPropagation();
  }

  return (
    <div
      className={overlayClassName ? `dialog-overlay ${overlayClassName}` : "dialog-overlay"}
      onClick={onClose}
      role="presentation"
    >
      <div
        aria-describedby={description ? descriptionId : undefined}
        aria-labelledby={titleId}
        aria-modal="true"
        className={className ? `dialog-card ${className}` : "dialog-card"}
        onClick={stopPropagation}
        role="dialog"
      >
        <div className="dialog-copy detail-dialog-heading">
          <div>
            <h3 id={titleId}>{title}</h3>
            {description ? <p id={descriptionId}>{description}</p> : null}
          </div>
          <VibeInlineButton
            aria-label={t("common.close")}
            className="detail-dialog-close"
            onClick={onClose}
            title={t("common.close")}
            variant="ghost"
          >
            <span aria-hidden="true">×</span>
          </VibeInlineButton>
        </div>

        <div className="detail-dialog-body">{children}</div>
      </div>
    </div>
  );
}

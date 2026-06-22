import { useEffect, useId, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

type VibeModalProps = {
  bodyClassName?: string;
  children: ReactNode;
  className?: string;
  closeLabel?: string;
  footer?: ReactNode;
  onClose: () => void;
  open: boolean;
  overlayClassName?: string;
  title: ReactNode;
};

export function VibeModal({
  bodyClassName,
  children,
  className,
  closeLabel,
  footer,
  onClose,
  open,
  overlayClassName,
  title,
}: VibeModalProps) {
  const { t } = useTranslation();
  const titleId = useId();
  const resolvedCloseLabel = closeLabel ?? t("common.close");

  useEffect(() => {
    if (!open) return undefined;

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        onClose();
      }
    }

    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [onClose, open]);

  if (!open) return null;

  return (
    <div
      className={["ref-modal-overlay", overlayClassName].filter(Boolean).join(" ")}
      role="presentation"
    >
      <section
        aria-labelledby={titleId}
        aria-modal="true"
        className={["ref-modal-card", className].filter(Boolean).join(" ")}
        role="dialog"
      >
        <header className="ref-modal-head">
          <strong id={titleId}>{title}</strong>
          <button aria-label={resolvedCloseLabel} onClick={onClose} type="button">
            ×
          </button>
        </header>
        <div className={["ref-modal-body", bodyClassName].filter(Boolean).join(" ")}>
          {children}
        </div>
        {footer ? <footer className="ref-modal-foot">{footer}</footer> : null}
      </section>
    </div>
  );
}

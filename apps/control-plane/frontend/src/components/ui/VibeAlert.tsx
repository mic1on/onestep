import type { AriaRole, ReactNode } from "react";

export type VibeAlertTone = "success" | "error" | "warning" | "info";

type VibeAlertProps = {
  tone?: VibeAlertTone;
  title: ReactNode;
  children?: ReactNode;
  className?: string;
  dismissLabel?: string;
  onDismiss?: () => void;
  role?: AriaRole;
};

export function VibeAlert({
  children,
  className,
  dismissLabel = "Dismiss",
  onDismiss,
  role,
  title,
  tone = "info",
}: VibeAlertProps) {
  const classes = ["vibe-alert", `vibe-alert-${tone}`, className].filter(Boolean).join(" ");

  return (
    <article className={classes} role={role}>
      <span className="vibe-alert-icon" aria-hidden="true" />
      <div className="vibe-alert-copy">
        <strong>{title}</strong>
        {children ? <p>{children}</p> : null}
      </div>
      {onDismiss ? (
        <button
          aria-label={dismissLabel}
          className="vibe-alert-close"
          onClick={onDismiss}
          type="button"
        >
          &times;
        </button>
      ) : null}
    </article>
  );
}

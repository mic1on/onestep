import type { ReactNode } from "react";

type EmptyStateProps = {
  title: string;
  body: string;
  action?: ReactNode;
  className?: string;
};

export function EmptyState({ title, body, action, className }: EmptyStateProps) {
  const classes = ["empty-state", action ? "empty-state-with-action" : undefined, className]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
      <div className="empty-state-visual" aria-hidden="true">
        <span className="empty-state-mark" />
      </div>
      <h3>{title}</h3>
      <p>{body}</p>
      {action ? <div className="empty-state-actions">{action}</div> : null}
    </div>
  );
}

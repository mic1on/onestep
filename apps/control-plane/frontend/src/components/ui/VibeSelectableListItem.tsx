import type { ReactNode } from "react";

type VibeSelectableListItemProps = {
  actions?: ReactNode;
  meta?: ReactNode;
  onSelect: () => void;
  selected?: boolean;
  title: ReactNode;
};

export function VibeSelectableListItem({
  actions,
  meta,
  onSelect,
  selected = false,
  title,
}: VibeSelectableListItemProps) {
  return (
    <article className={["notification-channel-card", selected ? "is-selected" : undefined].filter(Boolean).join(" ")}>
      <button className="notification-channel-main" onClick={onSelect} type="button">
        <div className="notification-channel-title-row">
          <strong>{title}</strong>
        </div>
        {meta ? <div className="notification-channel-meta">{meta}</div> : null}
      </button>
      {actions ? <div className="notification-channel-actions">{actions}</div> : null}
    </article>
  );
}

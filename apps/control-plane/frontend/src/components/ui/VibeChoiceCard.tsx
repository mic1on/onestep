import type { InputHTMLAttributes, ReactNode } from "react";

type VibeChoiceCardProps = Omit<
  InputHTMLAttributes<HTMLInputElement>,
  "children" | "className" | "title"
> & {
  className?: string;
  description?: ReactNode;
  title: ReactNode;
  toggle?: boolean;
};

export function VibeChoiceCard({
  checked,
  className,
  description,
  disabled,
  title,
  toggle = false,
  type = "checkbox",
  ...inputProps
}: VibeChoiceCardProps) {
  return (
    <label
      className={[
        "notification-choice-card",
        toggle ? "notification-choice-card-toggle" : undefined,
        checked ? "is-selected" : undefined,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <input
        checked={checked}
        className="notification-choice-input"
        disabled={disabled}
        type={type}
        {...inputProps}
      />
      <span className="notification-choice-indicator" aria-hidden="true" />
      <div className="notification-choice-copy">
        <strong>{title}</strong>
        {description === undefined || description === null ? null : <span>{description}</span>}
      </div>
    </label>
  );
}

import type { ButtonHTMLAttributes, ReactNode } from "react";

type VibeSwitchProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  checked: boolean;
  label: ReactNode;
  pending?: boolean;
};

export function VibeSwitch({
  checked,
  className,
  label,
  pending = false,
  ...props
}: VibeSwitchProps) {
  return (
    <button
      aria-pressed={checked}
      className={[
        "notification-channel-toggle",
        checked ? "is-enabled" : "is-disabled",
        pending ? "is-pending" : undefined,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      type="button"
      {...props}
    >
      <span className="notification-channel-toggle-dot" aria-hidden="true" />
      <span className="notification-channel-toggle-label">{label}</span>
    </button>
  );
}

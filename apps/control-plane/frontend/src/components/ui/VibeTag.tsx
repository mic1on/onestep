import type { ButtonHTMLAttributes, ReactNode } from "react";

type VibeTagGroupProps = {
  children: ReactNode;
  className?: string;
};

type VibeTagProps = {
  children: ReactNode;
  className?: string;
  variant?: "default" | "tasks";
};

type VibeFilterChipProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  active?: boolean;
  clear?: boolean;
  count?: ReactNode;
  label: ReactNode;
};

export function VibeTagGroup({ children, className }: VibeTagGroupProps) {
  return (
    <span className={["ref-service-tags", className].filter(Boolean).join(" ")}>
      {children}
    </span>
  );
}

export function VibeTag({ children, className, variant = "default" }: VibeTagProps) {
  return (
    <span
      className={[
        "ref-mini-tag",
        variant === "tasks" ? "ref-mini-tag-tasks" : undefined,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      {children}
    </span>
  );
}

export function VibeFilterChip({
  active = false,
  clear = false,
  className,
  count,
  label,
  ...props
}: VibeFilterChipProps) {
  return (
    <button
      aria-pressed={active}
      className={[
        "ref-tag-chip",
        active ? "is-active" : undefined,
        clear ? "ref-tag-chip-clear" : undefined,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      type="button"
      {...props}
    >
      <span className="ref-tag-chip-kind">{label}</span>
      {count === undefined || count === null ? null : (
        <span className="ref-tag-chip-count">{count}</span>
      )}
    </button>
  );
}

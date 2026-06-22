import type { ButtonHTMLAttributes, ReactNode } from "react";

type VibeButtonVariant = "primary" | "secondary" | "danger" | "dangerSolid" | "ghost";

type VibeButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: ReactNode;
  variant?: VibeButtonVariant;
  fullWidth?: boolean;
};

const variantClass: Record<VibeButtonVariant, string> = {
  primary: "vibe-button-primary",
  secondary: "vibe-button-secondary",
  danger: "vibe-button-danger",
  dangerSolid: "vibe-button-danger-solid",
  ghost: "vibe-button-ghost",
};

export function VibeButton({
  children,
  className,
  fullWidth = false,
  icon,
  variant = "secondary",
  ...props
}: VibeButtonProps) {
  const classes = [
    "vibe-button",
    variantClass[variant],
    fullWidth ? "vibe-button-full" : undefined,
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button className={classes} type="button" {...props}>
      {icon ? (
        <span aria-hidden="true" className="vibe-button-icon">
          {icon}
        </span>
      ) : null}
      <span className="vibe-button-label">{children}</span>
    </button>
  );
}

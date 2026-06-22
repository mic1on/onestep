import type { ButtonHTMLAttributes, ReactNode } from "react";

type VibeInlineButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  children: ReactNode;
  tone?: "default" | "danger";
  variant?: "text" | "ghost";
};

export function VibeInlineButton({
  children,
  className,
  tone = "default",
  variant = "text",
  ...props
}: VibeInlineButtonProps) {
  const baseClass = variant === "ghost" ? "ref-ghost-button" : "button-link";
  const toneClass = tone === "danger" ? "is-danger" : undefined;

  return (
    <button
      className={[baseClass, toneClass, className].filter(Boolean).join(" ")}
      type="button"
      {...props}
    >
      {children}
    </button>
  );
}

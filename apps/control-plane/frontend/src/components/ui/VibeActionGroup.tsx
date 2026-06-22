import type { ReactNode } from "react";

type VibeActionGroupVariant = "page" | "inline";

type VibeActionGroupProps = {
  children: ReactNode;
  className?: string;
  variant?: VibeActionGroupVariant;
};

const variantClass: Record<VibeActionGroupVariant, string> = {
  page: "ref-page-actions",
  inline: "page-actions-inline",
};

export function VibeActionGroup({
  children,
  className,
  variant = "page",
}: VibeActionGroupProps) {
  return (
    <div className={[variantClass[variant], className].filter(Boolean).join(" ")}>
      {children}
    </div>
  );
}

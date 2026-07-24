import type { ButtonHTMLAttributes, ReactNode } from "react";
import { clsx } from "clsx";

type IconButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  label: string;
  icon: ReactNode;
};

export function IconButton({ className, label, icon, ...props }: IconButtonProps) {
  return (
    <button aria-label={label} title={label} className={clsx("icon-button", className)} {...props}>
      {icon}
    </button>
  );
}

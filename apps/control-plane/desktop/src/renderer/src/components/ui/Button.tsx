import type { ButtonHTMLAttributes, ReactNode } from "react";
import { clsx } from "clsx";

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  icon?: ReactNode;
  variant?: "default" | "primary";
};

export function Button({ className, icon, variant = "default", children, ...props }: ButtonProps) {
  return (
    <button className={clsx("button", variant === "primary" && "primary", className)} {...props}>
      {icon}
      {children}
    </button>
  );
}

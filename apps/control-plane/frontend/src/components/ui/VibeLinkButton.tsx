import { Link, type LinkProps } from "react-router-dom";

type VibeLinkButtonProps = LinkProps & {
  tone?: "default" | "danger";
  variant?: "text" | "ghost";
};

export function VibeLinkButton({
  className,
  tone = "default",
  variant = "text",
  ...props
}: VibeLinkButtonProps) {
  const baseClass = variant === "ghost" ? "ref-ghost-button" : "button-link";
  const toneClass = tone === "danger" ? "is-danger" : undefined;

  return (
    <Link
      className={[baseClass, toneClass, className].filter(Boolean).join(" ")}
      {...props}
    />
  );
}

import type { HTMLAttributes, ReactNode } from "react";

type VibeInlineNoticeElement = "div" | "p" | "span";
type VibeInlineNoticeVariant = "empty" | "error" | "fanout" | "feedback";
type VibeInlineNoticeTone = "error" | "success";

type VibeInlineNoticeProps = HTMLAttributes<HTMLElement> & {
  as?: VibeInlineNoticeElement;
  children: ReactNode;
  tone?: VibeInlineNoticeTone;
  variant?: VibeInlineNoticeVariant;
};

const variantClass: Record<VibeInlineNoticeVariant, string> = {
  empty: "runtime-empty-inline",
  error: "ref-error-note",
  fanout: "fanout-note",
  feedback: "inline-feedback",
};

const toneClass: Record<VibeInlineNoticeTone, string> = {
  error: "inline-feedback-error",
  success: "inline-feedback-success",
};

export function VibeInlineNotice({
  as = "p",
  children,
  className,
  tone,
  variant = "empty",
  ...props
}: VibeInlineNoticeProps) {
  const Component = as;
  const feedbackClass = tone && variant === "fanout" ? "inline-feedback" : undefined;

  return (
    <Component
      className={[
        variantClass[variant],
        feedbackClass,
        tone ? toneClass[tone] : undefined,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
      {...props}
    >
      {children}
    </Component>
  );
}

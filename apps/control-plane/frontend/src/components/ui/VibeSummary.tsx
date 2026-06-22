import type { ReactNode } from "react";

export type VibeStatisticTone = "default" | "accent" | "success" | "danger";

export type VibeSummaryItem = {
  label: ReactNode;
  value: ReactNode;
  tone?: VibeStatisticTone;
  className?: string;
};

type VibeSummaryStripProps = {
  children?: ReactNode;
  className?: string;
  items?: VibeSummaryItem[];
};

type VibeStatisticProps = VibeSummaryItem;

export function VibeSummaryStrip({ children, className, items }: VibeSummaryStripProps) {
  return (
    <section className={["ref-summary-strip", className].filter(Boolean).join(" ")}>
      {items?.map((item, index) => (
        <VibeStatistic
          className={item.className}
          key={`${typeof item.label === "string" ? item.label : "summary"}-${index}`}
          label={item.label}
          tone={item.tone}
          value={item.value}
        />
      ))}
      {children}
    </section>
  );
}

export function VibeStatistic({
  className,
  label,
  tone = "default",
  value,
}: VibeStatisticProps) {
  return (
    <article className={["ref-summary-chip", `ref-summary-chip-${tone}`, className].filter(Boolean).join(" ")}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

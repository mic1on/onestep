import { clsx } from "clsx";

export type StatusTone = "neutral" | "success" | "warning" | "danger" | "info";

export function StatusDot({ tone = "neutral" }: { tone?: StatusTone }) {
  return <span className={clsx("status-dot", tone !== "neutral" && tone)} />;
}

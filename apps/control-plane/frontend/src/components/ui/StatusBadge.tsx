import { useTranslation } from "react-i18next";

type BadgeValue =
  | "active"
  | "accepted"
  | "cancelled"
  | "disconnected"
  | "dispatched"
  | "failed"
  | "online"
  | "offline"
  | "never_reported"
  | "ok"
  | "pending"
  | "rejected"
  | "degraded"
  | "error"
  | "starting"
  | "succeeded"
  | "superseded"
  | "timeout"
  | "unknown"
  | "consistent"
  | "drift";

type StatusBadgeProps = {
  value: BadgeValue;
  label?: string;
};

const BADGE_CLASS_MAP: Record<BadgeValue, string> = {
  active: "badge-success",
  accepted: "badge-accent",
  cancelled: "badge-warning",
  disconnected: "badge-muted",
  dispatched: "badge-accent",
  failed: "badge-danger",
  online: "badge-success",
  ok: "badge-success",
  pending: "badge-accent",
  rejected: "badge-danger",
  consistent: "badge-success",
  degraded: "badge-warning",
  offline: "badge-warning",
  starting: "badge-accent",
  succeeded: "badge-success",
  superseded: "badge-muted",
  timeout: "badge-danger",
  drift: "badge-danger",
  error: "badge-danger",
  unknown: "badge-muted",
  never_reported: "badge-muted",
};

export function StatusBadge({ value, label }: StatusBadgeProps) {
  const { t } = useTranslation();

  return <span className={`status-badge ${BADGE_CLASS_MAP[value]}`}>{label ?? t(`status.${value}`)}</span>;
}

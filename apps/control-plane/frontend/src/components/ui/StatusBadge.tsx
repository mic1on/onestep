import { useTranslation } from "react-i18next";

type BadgeValue =
  | "online"
  | "offline"
  | "never_reported"
  | "ok"
  | "degraded"
  | "error"
  | "starting"
  | "unknown"
  | "consistent"
  | "drift";

type StatusBadgeProps = {
  value: BadgeValue;
  label?: string;
};

const BADGE_CLASS_MAP: Record<BadgeValue, string> = {
  online: "badge-success",
  ok: "badge-success",
  consistent: "badge-success",
  degraded: "badge-warning",
  offline: "badge-warning",
  starting: "badge-accent",
  drift: "badge-danger",
  error: "badge-danger",
  unknown: "badge-muted",
  never_reported: "badge-muted",
};

export function StatusBadge({ value, label }: StatusBadgeProps) {
  const { t } = useTranslation();

  return <span className={`status-badge ${BADGE_CLASS_MAP[value]}`}>{label ?? t(`status.${value}`)}</span>;
}

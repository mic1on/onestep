import i18n, { getCurrentIntlLocale } from "./i18n";

export function formatDateTime(value: string | null | undefined) {
  if (!value) {
    return i18n.t("common.never");
  }

  return new Intl.DateTimeFormat(getCurrentIntlLocale(), {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatRelativeTime(value: string | null | undefined) {
  if (!value) {
    return i18n.t("common.never");
  }

  const diffMs = new Date(value).getTime() - Date.now();
  const formatter = new Intl.RelativeTimeFormat(getCurrentIntlLocale(), { numeric: "auto" });
  const diffMinutes = Math.round(diffMs / 60_000);
  if (Math.abs(diffMinutes) < 60) {
    return formatter.format(diffMinutes, "minute");
  }
  const diffHours = Math.round(diffMs / 3_600_000);
  if (Math.abs(diffHours) < 48) {
    return formatter.format(diffHours, "hour");
  }
  const diffDays = Math.round(diffMs / 86_400_000);
  return formatter.format(diffDays, "day");
}

export function formatCount(value: number) {
  return new Intl.NumberFormat(getCurrentIntlLocale()).format(value);
}

export function formatDurationMs(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "n/a";
  }
  if (value >= 1_000) {
    return `${(value / 1_000).toFixed(1)}s`;
  }
  return `${value.toFixed(0)}ms`;
}

export function formatCompactJson(value: unknown) {
  return JSON.stringify(value, null, 2);
}

export function formatIdentifierPreview(value: string | null | undefined, prefix = 18, suffix = 10) {
  if (!value) {
    return i18n.t("common.notAvailable");
  }

  if (value.length <= prefix + suffix + 3) {
    return value;
  }

  return `${value.slice(0, prefix)}...${value.slice(-suffix)}`;
}

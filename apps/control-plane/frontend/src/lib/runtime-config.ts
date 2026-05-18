type RuntimeConfig = {
  apiBaseUrl?: string;
};

const DEFAULT_INACTIVE_SERVICE_DAYS = 3;

declare global {
  interface Window {
    __APP_CONFIG__?: RuntimeConfig;
  }
}

function normalizeApiBaseUrl(value: string | undefined) {
  if (!value) {
    return undefined;
  }
  return value.replace(/\/$/, "");
}

function parsePositiveNumber(value: string | undefined) {
  if (!value) {
    return undefined;
  }

  const parsed = Number(value);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return undefined;
  }

  return parsed;
}

export function getApiBaseUrl() {
  return (
    normalizeApiBaseUrl(window.__APP_CONFIG__?.apiBaseUrl) ??
    normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL) ??
    window.location.origin
  );
}

export function getInactiveServiceDays() {
  return parsePositiveNumber(import.meta.env.VITE_SERVICE_INACTIVE_DAYS) ?? DEFAULT_INACTIVE_SERVICE_DAYS;
}

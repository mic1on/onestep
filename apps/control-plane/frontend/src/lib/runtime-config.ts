type RuntimeConfig = {
  apiBaseUrl?: string;
};

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

export function getApiBaseUrl() {
  return (
    normalizeApiBaseUrl(window.__APP_CONFIG__?.apiBaseUrl) ??
    normalizeApiBaseUrl(import.meta.env.VITE_API_BASE_URL) ??
    window.location.origin
  );
}

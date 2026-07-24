declare global {
  interface Window {
    onestepDesktop?: {
      platform: string;
      apiBaseUrl: string;
    };
  }
}

export function getApiBaseUrl(): string {
  const fromPreload = window.onestepDesktop?.apiBaseUrl;
  if (!fromPreload) {
    throw new Error("OneStep desktop API base URL is unavailable");
  }
  return fromPreload.replace(/\/$/, "");
}

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    credentials: "include",
  });
  if (!response.ok) {
    throw new Error(`GET ${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${getApiBaseUrl()}${path}`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    throw new Error(`POST ${path} failed with ${response.status}`);
  }
  return (await response.json()) as T;
}

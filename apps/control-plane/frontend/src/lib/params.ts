import type { Environment } from "./api/types";

const ENVIRONMENTS: Environment[] = ["dev", "staging", "prod"];
const LOOKBACK_OPTIONS = [15, 60, 360, 1440];

export function parseEnvironment(
  searchParams: URLSearchParams,
  fallback: Environment = "prod",
): Environment {
  const value = searchParams.get("environment");
  if (value && ENVIRONMENTS.includes(value as Environment)) {
    return value as Environment;
  }
  return fallback;
}

export function parseLookback(searchParams: URLSearchParams, fallback = 60) {
  const value = Number(searchParams.get("lookback_minutes"));
  if (LOOKBACK_OPTIONS.includes(value)) {
    return value;
  }
  return fallback;
}

export function createSearch(params: Record<string, string | number | undefined | null>) {
  const next = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") {
      continue;
    }
    next.set(key, String(value));
  }
  const query = next.toString();
  return query ? `?${query}` : "";
}

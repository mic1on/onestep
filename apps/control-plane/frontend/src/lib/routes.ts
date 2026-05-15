import type { Environment } from "./api/types";
import { createSearch } from "./params";

type DetailParams = {
  environment: Environment;
  lookback_minutes?: number;
  tab?: string;
};

export function servicePath(serviceName: string, params: DetailParams) {
  return `/services/${encodeURIComponent(serviceName)}${createSearch(params)}`;
}

export function taskPath(
  serviceName: string,
  taskName: string,
  params: DetailParams,
) {
  return `/services/${encodeURIComponent(serviceName)}/tasks/${encodeURIComponent(taskName)}${createSearch(params)}`;
}

export function instancePath(
  serviceName: string,
  instanceId: string,
  params: Omit<DetailParams, "tab">,
) {
  return `/services/${encodeURIComponent(serviceName)}/instances/${encodeURIComponent(instanceId)}${createSearch(params)}`;
}

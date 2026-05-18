import { afterEach, describe, expect, it, vi } from "vitest";

import { getInactiveServiceDays } from "./runtime-config";

describe("getInactiveServiceDays", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it("returns the configured positive env value", () => {
    vi.stubEnv("VITE_SERVICE_INACTIVE_DAYS", "5");

    expect(getInactiveServiceDays()).toBe(5);
  });

  it("falls back to 3 when the env value is invalid", () => {
    vi.stubEnv("VITE_SERVICE_INACTIVE_DAYS", "abc");

    expect(getInactiveServiceDays()).toBe(3);
  });

  it("falls back to 3 when the env value is empty", () => {
    vi.stubEnv("VITE_SERVICE_INACTIVE_DAYS", "");

    expect(getInactiveServiceDays()).toBe(3);
  });
});

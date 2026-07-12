import { describe, expect, it } from "vitest";
import { healthTone } from "../src/renderer/src/lib/status";

describe("healthTone", () => {
  it("maps operational statuses to visual tones", () => {
    expect(healthTone("healthy")).toBe("success");
    expect(healthTone("pending")).toBe("warning");
    expect(healthTone("failed")).toBe("danger");
    expect(healthTone("unknown")).toBe("neutral");
  });
});

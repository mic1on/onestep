import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Vitest defaults NODE_ENV to "test" only when unset. A shell exporting
// NODE_ENV=production leaks through, which makes React load its production
// build (no `act` export) and breaks every component test with
// `React.act is not a function`. Force "test" so the suite is hermetic.
process.env.NODE_ENV = "test";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.ts", "src/**/*.test.tsx"],
    exclude: ["e2e/**"],
    passWithNoTests: true,
  },
});

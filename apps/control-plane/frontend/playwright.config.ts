import { defineConfig } from "@playwright/test";

const PLAYWRIGHT_PORT = 43017;

export default defineConfig({
  testDir: "./e2e",
  retries: process.env.CI ? 2 : 0,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL ?? `http://127.0.0.1:${PLAYWRIGHT_PORT}`,
  },
  webServer: {
    command: `pnpm build && pnpm preview --host 127.0.0.1 --port ${PLAYWRIGHT_PORT} --strictPort`,
    port: PLAYWRIGHT_PORT,
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});

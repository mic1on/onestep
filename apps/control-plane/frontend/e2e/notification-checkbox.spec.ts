import { expect, test } from "@playwright/test";

const ISO_NOW = "2026-07-16T08:00:00Z";

const service = {
  name: "service-01",
  environment: "dev",
  latest_deployment_version: "2026.7.16",
  service_status: "online",
  latest_topology_hash: "topo-service-01",
  latest_sync_at: ISO_NOW,
  instance_count: 1,
  online_instance_count: 1,
  last_seen_at: ISO_NOW,
  source_kinds: ["schedule"],
  task_count: 0,
  failing_task_count: 0,
  view_status: "running",
  success_rate: 100,
  throughput_per_min: 0,
  error_count: 0,
  uptime_reference_at: ISO_NOW,
  online_task_count: 0,
  standby_instance_count: 0,
  created_at: ISO_NOW,
  updated_at: ISO_NOW,
};

test("service checkbox clicks do not scroll the notifications page", async ({ page }) => {
  await page.route("**/api/v1/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;

    if (path === "/api/v1/auth/session") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          auth_configured: true,
          bootstrap_required: false,
          authenticated: false,
          username: null,
          role: null,
          roles: [],
        }),
      });
      return;
    }

    if (path === "/api/v1/services") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [service],
          total: 1,
          limit: 100,
          offset: 0,
          source_kind_counts: { schedule: 1 },
          summary: {
            total_services: 1,
            online_services: 1,
            attention_services: 0,
            offline_services: 0,
            ready_services: 1,
            total_instances: 1,
            online_instances: 1,
            total_tasks: 0,
            failing_tasks: 0,
          },
        }),
      });
      return;
    }

    if (path === "/api/v1/services/service-01/dashboard") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          service,
          lookback_minutes: 15,
          lookback_started_at: ISO_NOW,
          task_count: 0,
          failing_task_count: 0,
          recent_events: [],
        }),
      });
      return;
    }

    if (path === "/api/v1/services/service-01/tasks" || path === "/api/v1/services/service-01/instances") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: [],
          total: 0,
          limit: 100,
          offset: 0,
          lookback_minutes: 15,
          lookback_started_at: ISO_NOW,
        }),
      });
      return;
    }

    if (path === "/api/v1/settings/notifications/channels") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({ items: [] }),
      });
      return;
    }

    if (path === "/api/v1/settings/notifications/services") {
      await route.fulfill({
        contentType: "application/json",
        body: JSON.stringify({
          items: Array.from({ length: 40 }, (_, index) => ({
            environment: "dev",
            name: `service-${String(index + 1).padStart(2, "0")}`,
          })),
        }),
      });
      return;
    }

    await route.fulfill({
      status: 404,
      contentType: "application/json",
      body: JSON.stringify({ detail: `Unhandled test route: ${path}` }),
    });
  });

  await page.goto("/notifications");
  await page.waitForFunction(() => document.body.textContent?.includes("dev/service-01"));
  await page.evaluate(() => window.scrollTo(0, 0));

  await page.evaluate(() => {
    const serviceText = [...document.querySelectorAll("span")].find(
      (element) => element.textContent?.trim() === "dev/service-01",
    );
    serviceText?.closest("label")?.click();
  });

  await expect.poll(() => page.evaluate(() => window.scrollY)).toBe(0);
  await expect
    .poll(() =>
      page.evaluate(() => {
        const serviceText = [...document.querySelectorAll("span")].find(
          (element) => element.textContent?.trim() === "dev/service-01",
        );
        return serviceText?.closest("label")?.querySelector("input")?.checked ?? false;
      }),
    )
    .toBe(true);
});

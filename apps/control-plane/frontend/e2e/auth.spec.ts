import { expect, test, type Page, type Route } from "@playwright/test";

type ConsoleRole = "viewer" | "operator" | "admin";
type SessionState = {
  auth_configured: boolean;
  bootstrap_required: boolean;
  authenticated: boolean;
  username: string | null;
  role: ConsoleRole | null;
  roles: ConsoleRole[];
};

const SERVICE_NAME = "billing-service";
const SERVICE_PATH = `/services/${SERVICE_NAME}?environment=prod`;

function isoMinutesAgo(minutes: number) {
  return new Date(Date.now() - minutes * 60 * 1000).toISOString();
}

function buildSession(role: ConsoleRole | null, authenticated = true): SessionState {
  return {
    auth_configured: true,
    bootstrap_required: false,
    authenticated,
    username: authenticated && role ? `${role}-user` : null,
    role,
    roles: role ? [role] : [],
  };
}

function json(route: Route, body: unknown, status = 200) {
  return route.fulfill({
    status,
    contentType: "application/json",
    body: JSON.stringify(body),
  });
}

function buildServiceSummary() {
  return {
    name: SERVICE_NAME,
    environment: "prod",
    latest_deployment_version: "2026.05.15",
    latest_topology_hash: "topo_abc123",
    latest_sync_at: isoMinutesAgo(0),
    instance_count: 1,
    online_instance_count: 1,
    last_seen_at: isoMinutesAgo(0),
    source_kinds: ["cron"],
    task_count: 1,
    created_at: isoMinutesAgo(5),
    updated_at: isoMinutesAgo(0),
  };
}

function buildServicesResponse() {
  return {
    items: [buildServiceSummary()],
    total: 1,
    limit: 100,
    offset: 0,
    source_kind_counts: {
      cron: 1,
    },
  };
}

function buildServiceDashboardResponse() {
  return {
    service: buildServiceSummary(),
    lookback_minutes: 60,
    lookback_started_at: isoMinutesAgo(60),
    instance_connectivity: {
      total: 1,
      online: 1,
      offline: 0,
      never_reported: 0,
    },
    instance_statuses: {
      ok: 1,
      degraded: 0,
      error: 0,
      starting: 0,
      unknown: 0,
    },
    task_count: 1,
    failing_task_count: 0,
    command_overview: {
      statuses: {
        pending: 0,
        dispatched: 0,
        accepted: 0,
        expired: 0,
        rejected: 0,
        succeeded: 0,
        failed: 0,
        timeout: 0,
        cancelled: 0,
        in_flight: 0,
        total: 0,
      },
      active_session_count: 1,
      last_command_at: null,
      last_completed_at: null,
    },
    topology_hashes: ["topo_abc123"],
    topology_consistent: true,
    recent_events: [],
  };
}

function buildEmptyPaginatedResponse(limit: number) {
  return {
    items: [],
    total: 0,
    limit,
    offset: 0,
  };
}

function buildInstancesResponse() {
  return {
    items: [
      {
        instance_id: "inst_1",
        node_name: "node-a",
        hostname: "host-a",
        pid: null,
        deployment_version: "2026.05.15",
        onestep_version: null,
        python_version: null,
        started_at: null,
        last_sync_at: isoMinutesAgo(0),
        last_topology_hash: "topo_abc123",
        last_heartbeat_sent_at: null,
        last_heartbeat_sequence: null,
        last_seen_at: isoMinutesAgo(0),
        status: "ok",
        connectivity: "online",
        active_session: null,
        created_at: isoMinutesAgo(5),
        updated_at: isoMinutesAgo(0),
      },
    ],
    total: 1,
    limit: 100,
    offset: 0,
  };
}

async function installCommonRoutes(page: Page, options: { initialSession: SessionState; loginSession?: SessionState }) {
  let session = options.initialSession;

  await page.addInitScript(() => {
    window.localStorage.setItem("onestep-control-plane.locale", "en");
  });

  await page.route("**/api/v1/ui/stream", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "text/event-stream",
      body: "",
      headers: {
        "Cache-Control": "no-cache",
        Connection: "keep-alive",
      },
    });
  });

  await page.route("**/api/v1/auth/session", async (route) => {
    await json(route, session);
  });

  await page.route("**/api/v1/auth/login", async (route) => {
    const payload = (await route.request().postDataJSON()) as {
      username?: string;
      password?: string;
    };
    expect(payload.username).toBeTruthy();
    expect(payload.password).toBeTruthy();
    session = options.loginSession ?? buildSession("admin", true);
    await json(route, session);
  });

  await page.route("**/api/v1/auth/logout", async (route) => {
    session = buildSession(null, false);
    await json(route, session);
  });

  await page.route("**/api/v1/services?**", async (route) => {
    await json(route, buildServicesResponse());
  });

  await page.route(`**/api/v1/services/${SERVICE_NAME}/dashboard?**`, async (route) => {
    await json(route, buildServiceDashboardResponse());
  });

  await page.route(`**/api/v1/services/${SERVICE_NAME}/tasks?**`, async (route) => {
    await json(route, buildEmptyPaginatedResponse(100));
  });

  await page.route(`**/api/v1/services/${SERVICE_NAME}/commands?**`, async (route) => {
    await json(route, buildEmptyPaginatedResponse(50));
  });

  await page.route(`**/api/v1/services/${SERVICE_NAME}/sessions?**`, async (route) => {
    await json(route, buildEmptyPaginatedResponse(50));
  });

  await page.route(`**/api/v1/services/${SERVICE_NAME}/instances?**`, async (route) => {
    await json(route, buildInstancesResponse());
  });
}

test.describe("console auth", () => {
  test("redirects unauthenticated users to login", async ({ page }) => {
    await installCommonRoutes(page, {
      initialSession: buildSession(null, false),
    });

    await page.goto("/services?environment=all&lang=en");

    await expect(page).toHaveURL(/\/login\?next=/);
    await expect(page.getByRole("heading", { name: "Console sign in" })).toBeVisible();
  });

  test("signs in successfully and lands on the next page", async ({ page }) => {
    await installCommonRoutes(page, {
      initialSession: buildSession(null, false),
      loginSession: buildSession("admin", true),
    });

    await page.goto(`/login?lang=en&next=${encodeURIComponent("/services?environment=all")}`);

    await page.getByLabel("Username").fill("admin-user");
    await page.getByLabel("Password").fill("super-secret-pass");
    await page.getByRole("button", { name: "Sign in" }).click();

    await expect(page).toHaveURL(/\/services\?environment=all/);
    await expect(page.getByRole("heading", { name: "Service fleet" })).toBeVisible();
    await expect(page.getByText("billing-service")).toBeVisible();
    await expect(page.getByText("admin-user")).toBeVisible();
  });

  test("hides control buttons for a viewer account", async ({ page }) => {
    await installCommonRoutes(page, {
      initialSession: buildSession("viewer", true),
    });

    await page.goto(`${SERVICE_PATH}&lang=en`);

    await expect(page.getByRole("heading", { name: SERVICE_NAME })).toBeVisible();
    await expect(page.getByRole("button", { name: "Sync data" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "restart" })).toHaveCount(0);
  });
});

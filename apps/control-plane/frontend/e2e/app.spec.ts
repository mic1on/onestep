import { expect, test, type Page } from "@playwright/test";

const ISO_NOW = "2026-07-16T08:00:00Z";

const billingService = {
  name: "billing-sync",
  environment: "prod",
  latest_deployment_version: "2026.7.16",
  service_status: "online",
  latest_topology_hash: "topo-billing",
  latest_sync_at: ISO_NOW,
  instance_count: 2,
  online_instance_count: 2,
  last_seen_at: ISO_NOW,
  source_kinds: ["redis_stream"],
  task_count: 2,
  created_at: ISO_NOW,
  updated_at: ISO_NOW,
};

const auditService = {
  name: "audit-sync",
  environment: "staging",
  latest_deployment_version: "2026.7.15",
  service_status: "attention",
  latest_topology_hash: "topo-audit",
  latest_sync_at: ISO_NOW,
  instance_count: 1,
  online_instance_count: 0,
  last_seen_at: ISO_NOW,
  source_kinds: ["schedule"],
  task_count: 1,
  created_at: ISO_NOW,
  updated_at: ISO_NOW,
};

const eventCounts = {
  failed: 0,
  retried: 0,
  dead_lettered: 0,
  cancelled: 0,
  succeeded: 1,
};

function taskEvent(overrides: Record<string, unknown> = {}) {
  return {
    event_id: "evt-1",
    instance_id: "11111111-1111-1111-1111-111111111111",
    task_name: "orders_to_ledger",
    kind: "succeeded",
    occurred_at: ISO_NOW,
    attempts: 1,
    duration_ms: 42,
    failure_kind: null,
    exception_type: null,
    message: "processed order batch",
    traceback: null,
    meta: {},
    received_at: ISO_NOW,
    created_at: ISO_NOW,
    ...overrides,
  };
}

function taskSummary(overrides: Record<string, unknown> = {}) {
  return {
    task_name: "orders_to_ledger",
    description: null,
    source_name: "orders",
    source_kind: "redis_stream",
    source_config: { stream: "orders.v1", group: "billing-workers" },
    emit: [{ kind: "http_sink", name: "ledger-api", config: { url: "https://ledger.internal/events" } }],
    concurrency: 4,
    timeout_s: 30,
    retry_policy: { kind: "exponential", config: { attempts: 3 } },
    topology_hash: "task-topo-orders",
    metric_window_count: 1,
    latest_window_started_at: ISO_NOW,
    latest_window_ended_at: ISO_NOW,
    fetched: 600,
    started: 600,
    succeeded: 590,
    retried: 5,
    failed: 0,
    dead_lettered: 0,
    cancelled: 0,
    timeouts: 0,
    weighted_avg_duration_ms: 42,
    max_p95_duration_ms: 120,
    last_event_at: ISO_NOW,
    event_counts: { ...eventCounts, retried: 5, succeeded: 590 },
    ...overrides,
  };
}

function metricWindow(overrides: Record<string, unknown> = {}) {
  return {
    instance_id: "11111111-1111-1111-1111-111111111111",
    task_name: "orders_to_ledger",
    window_id: "orders_to_ledger:0800",
    window_started_at: "2026-07-16T07:59:00Z",
    window_ended_at: ISO_NOW,
    fetched: 600,
    started: 600,
    succeeded: 590,
    retried: 5,
    failed: 0,
    dead_lettered: 0,
    cancelled: 0,
    timeouts: 0,
    inflight: 3,
    avg_duration_ms: 42,
    p95_duration_ms: 120,
    received_at: ISO_NOW,
    created_at: ISO_NOW,
    ...overrides,
  };
}

function instanceSummary(overrides: Record<string, unknown> = {}) {
  return {
    instance_id: "11111111-1111-1111-1111-111111111111",
    node_name: "worker-a",
    hostname: "worker-a.local",
    pid: 4242,
    deployment_version: "2026.7.16",
    onestep_version: "1.4.0",
    python_version: "3.12.4",
    started_at: ISO_NOW,
    last_sync_at: ISO_NOW,
    last_topology_hash: "task-topo-orders",
    last_heartbeat_sent_at: ISO_NOW,
    last_heartbeat_sequence: 12,
    last_seen_at: ISO_NOW,
    status: "ok",
    connectivity: "online",
    active_session: null,
    created_at: ISO_NOW,
    updated_at: ISO_NOW,
    ...overrides,
  };
}

async function installApiMocks(page: Page) {
  const commands: Array<{ method: string; path: string; query: Record<string, string>; body: unknown }> = [];
  let serviceListHits = 0;

  const serviceData = {
    "billing-sync": {
      service: billingService,
      dashboard: {
        service: billingService,
        lookback_minutes: 60,
        lookback_started_at: ISO_NOW,
        task_count: 2,
        failing_task_count: 1,
        recent_events: [
          taskEvent(),
          taskEvent({
            event_id: "evt-2",
            kind: "failed",
            message: "sink timeout",
            exception_type: "TimeoutError",
          }),
        ],
      },
      tasks: [
        taskSummary(),
        taskSummary({
          task_name: "invoice_retry",
          source_name: "retry-queue",
          source_kind: "schedule",
          source_config: { schedule: "*/5 * * * *" },
          emit: [{ kind: "http_sink", name: "invoice-api", config: { url: "https://invoice.internal/retry" } }],
          concurrency: 2,
          failed: 0,
          dead_lettered: 0,
          timeouts: 0,
          event_counts: { ...eventCounts, succeeded: 120 },
        }),
      ],
      instances: [
        instanceSummary(),
        instanceSummary({
          instance_id: "22222222-2222-2222-2222-222222222222",
          node_name: "worker-b",
          hostname: "worker-b.local",
          pid: 4343,
          status: "degraded",
          connectivity: "online",
        }),
      ],
    },
    "audit-sync": {
      service: auditService,
      dashboard: {
        service: auditService,
        lookback_minutes: 60,
        lookback_started_at: ISO_NOW,
        task_count: 1,
        failing_task_count: 1,
        recent_events: [taskEvent({ event_id: "evt-audit", task_name: "audit_archive", kind: "retried" })],
      },
      tasks: [
        taskSummary({
          task_name: "audit_archive",
          source_name: "daily-audit",
          source_kind: "schedule",
          source_config: { schedule: "0 2 * * *" },
          emit: [{ kind: "http_sink", name: "archive-api", config: { url: "https://archive.internal/audit" } }],
          failed: 1,
          dead_lettered: 0,
          timeouts: 0,
          event_counts: { ...eventCounts, failed: 1 },
        }),
      ],
      instances: [
        instanceSummary({
          instance_id: "33333333-3333-3333-3333-333333333333",
          node_name: "audit-worker",
          hostname: "audit-worker.local",
          pid: 4444,
          status: "error",
          connectivity: "offline",
        }),
      ],
    },
  };

  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const query = Object.fromEntries(url.searchParams.entries());

    if (request.method() === "POST") {
      commands.push({
        method: request.method(),
        path,
        query,
        body: request.postDataJSON(),
      });
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          command_id: "99999999-9999-9999-9999-999999999999",
          status: "queued",
          counts: { dispatched: 0, queued: 1, skipped: 0, rejected: 0, total: 1 },
          noop_reason_code: null,
          noop_reason_message: null,
        }),
      });
      return;
    }

    if (path === "/api/v1/services") {
      serviceListHits += 1;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [billingService, auditService],
          total: 2,
          limit: 100,
          offset: 0,
          source_kind_counts: { redis_stream: 1, schedule: 1 },
        }),
      });
      return;
    }

    const taskDetailMatch = path.match(/^\/api\/v1\/services\/([^/]+)\/tasks\/([^/]+)$/);
    if (taskDetailMatch) {
      const serviceName = decodeURIComponent(taskDetailMatch[1]);
      const taskName = decodeURIComponent(taskDetailMatch[2]);
      const data = serviceData[serviceName as keyof typeof serviceData];
      const task = data?.tasks.find((item) => item.task_name === taskName);
      if (!data || !task) {
        await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "missing" }) });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          service: data.service,
          task_name: taskName,
          lookback_minutes: 60,
          lookback_started_at: ISO_NOW,
          summary: task,
          task_control: { task_name: taskName, instances: [] },
          recent_metric_windows: [
            metricWindow({
              task_name: taskName,
              window_id: `${taskName}:0750`,
              window_started_at: "2026-07-16T07:49:00Z",
              window_ended_at: "2026-07-16T07:50:00Z",
              fetched: 420,
              succeeded: 418,
              failed: 1,
              p95_duration_ms: 96,
            }),
            metricWindow({ task_name: taskName, window_id: `${taskName}:0800` }),
          ],
          recent_events: [],
        }),
      });
      return;
    }

    const serviceMatch = path.match(/^\/api\/v1\/services\/([^/]+)\/(dashboard|tasks|instances)$/);
    if (serviceMatch) {
      const serviceName = decodeURIComponent(serviceMatch[1]);
      const resource = serviceMatch[2] as "dashboard" | "tasks" | "instances";
      const data = serviceData[serviceName as keyof typeof serviceData];
      if (!data) {
        await route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "missing" }) });
        return;
      }

      if (resource === "dashboard") {
        await route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(data.dashboard) });
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: data[resource],
          total: data[resource].length,
          limit: 100,
          offset: 0,
          ...(resource === "tasks" ? { lookback_minutes: 60, lookback_started_at: ISO_NOW } : {}),
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

  return {
    commands,
    get serviceListHits() {
      return serviceListHits;
    },
  };
}

test("renders the control plane dashboard", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");

  await expect(page.getByText("Service Detail")).toBeVisible();
  await expect(page.getByRole("button", { name: "Production" }).first()).toBeVisible();
  await expect(page.getByText("billing-sync").first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Deploy Update" })).toBeVisible();
});

test("does not render demo services while the initial API load is pending", async ({ page }) => {
  let releaseApi: () => void = () => {};
  const apiPending = new Promise<void>((resolve) => {
    releaseApi = resolve;
  });

  await page.route("**/api/v1/**", async (route) => {
    await apiPending;
    await route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({ detail: "test intentionally released pending API" }),
    });
  });

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Connecting to API" })).toBeVisible();
  await expect(page.getByText("user-auth-service")).toHaveCount(0);
  await expect(page.getByText("Ingest-Logs")).toHaveCount(0);

  releaseApi();
});

test("loads API-backed service, task, instance, topology, config, and log views", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");

  await expect(page.getByText("API connected")).toBeVisible();
  await expect(page.getByRole("button", { name: "Production" }).first()).toBeVisible();
  await expect(page.getByText("billing-sync").first()).toBeVisible();
  await expect(page.getByText("orders_to_ledger").first()).toBeVisible();
  await expect(page.getByText("redis_stream")).toBeVisible();
  await expect(page.getByText("ledger-api")).toBeVisible();

  await page.getByText("orders_to_ledger").first().click();
  await expect(page.getByRole("heading", { name: "orders_to_ledger" })).toBeVisible();
  await expect(page.getByText("Task Metrics (Last 1h)")).toBeVisible();
  await expect(page.getByText("1020")).toBeVisible();
  await expect(page.getByRole("button", { name: "Failures" })).toBeVisible();

  await page.getByText("View traces ->").click();
  await expect(page.getByText("Active Latency Traces")).toBeVisible();

  await page.getByRole("button", { name: "redis_stream orders" }).click();
  await expect(page.getByText("redis_stream Source Protocol")).toBeVisible();
  await page.getByRole("button", { name: "orders_to_ledger Task Processing" }).click();
  await expect(page.getByText("orders_to_ledger Stream Thread")).toBeVisible();
  await page.getByRole("button", { name: "http_sink ledger-api" }).click();
  await expect(page.getByText("http_sink Analytics Engine")).toBeVisible();

  await page.getByRole("button", { name: "Modify" }).click();
  await page.locator("textarea").fill(`task_config:\n  id: "orders_to_ledger"\n  execution:\n    concurrency: 7`);
  await page.getByRole("button", { name: "Save" }).click();
  await expect(page.getByText("Configuration update saved for orders_to_ledger")).toBeVisible();
  await expect(page.getByText("concurrency")).toBeVisible();
  await expect(page.getByText("7", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Instances" }).click();
  await expect(page.getByText("worker-a.local")).toBeVisible();
  await page.getByPlaceholder("Filter instances...").fill("worker-b");
  await expect(page.getByText("worker-b.local")).toBeVisible();
  await expect(page.getByText("worker-a.local")).toHaveCount(0);
  await page.getByPlaceholder("Filter instances...").fill("");

  await page.getByRole("button", { name: "Status: All" }).click();
  await page.getByRole("button", { name: "Failed" }).click();
  await expect(page.getByText("worker-b.local")).toBeVisible();
  await expect(page.getByText("worker-a.local")).toHaveCount(0);

  await page.getByRole("button", { name: "Export" }).click();
  await expect(page.getByText("Spreadsheet download initiated successfully")).toBeVisible();

  await page.getByRole("button", { name: "Logs" }).click();
  await expect(page.getByText("processed order batch")).toBeVisible();
  await page.getByRole("button", { name: "Clear stream" }).click();
  await expect(page.getByText("Kafka Consumer initialized successfully.")).toBeVisible();

  await page.getByRole("button", { name: "Configuration" }).click();
  await expect(page.getByText("Global Service Spec")).toBeVisible();
  await page.getByRole("button", { name: "Modify Global Config" }).click();
  await expect(page.getByText("Global Service Spec is read-only")).toBeVisible();
});

test("dispatches service, task, and instance commands to the control-plane API", async ({ page }) => {
  const api = await installApiMocks(page);
  await page.goto("/");
  await expect(page.getByText("API connected")).toBeVisible();

  await page.getByRole("button", { name: "Restart All" }).click();
  await expect(page.getByText("Restart command accepted for billing-sync / prod.")).toBeVisible();

  await page.getByRole("button", { name: "Deploy Update" }).click();
  await expect(page.getByText("Sync command accepted for billing-sync / prod.")).toBeVisible();

  await page.getByText("orders_to_ledger").first().click();
  await page.getByRole("button", { name: "Restart" }).click();
  await expect(page.getByText("orders_to_ledger restart sequence accepted.")).toBeVisible();

  await page.getByRole("button", { name: "Stop Task" }).click();
  await expect(page.getByText("orders_to_ledger command accepted.")).toBeVisible();

  await page.getByRole("button", { name: "Instances" }).click();
  await page.getByTitle("Restart Instance").first().click();
  await expect(page.getByText("Restart command accepted for [111111].")).toBeVisible();

  await page.getByTitle("Stop Instance").first().click();
  await expect(page.getByText("Shutdown command accepted for [111111].")).toBeVisible();

  expect(api.commands.map((command) => [command.path, (command.body as { kind: string }).kind])).toEqual([
    ["/api/v1/services/billing-sync/commands", "restart"],
    ["/api/v1/services/billing-sync/commands", "sync_now"],
    ["/api/v1/services/billing-sync/tasks/orders_to_ledger/commands", "pause_task"],
    ["/api/v1/services/billing-sync/tasks/orders_to_ledger/commands", "resume_task"],
    ["/api/v1/services/billing-sync/tasks/orders_to_ledger/commands", "pause_task"],
    ["/api/v1/instances/11111111-1111-1111-1111-111111111111/commands", "restart"],
    ["/api/v1/instances/11111111-1111-1111-1111-111111111111/commands", "shutdown"],
  ]);
  expect(api.commands[0].query).toEqual({ environment: "prod" });
  expect(api.commands[2].query).toEqual({ environment: "prod" });
  expect(api.commands[5].query).toEqual({});
});

test("switches service context and refreshes from the selected service endpoints", async ({ page }) => {
  const api = await installApiMocks(page);
  await page.goto("/");
  await expect(page.getByText("orders_to_ledger").first()).toBeVisible();

  await page.getByRole("button", { name: "Global Overview" }).click();
  await page.getByText("audit-sync / staging").click();

  await expect(page.getByText("audit-sync").first()).toBeVisible();
  await expect(page.getByText("audit_archive").first()).toBeVisible();
  await expect(page.getByText("orders_to_ledger")).toHaveCount(0);

  const hitsBeforeRefresh = api.serviceListHits;
  await page.getByRole("button", { name: "Refresh" }).click();
  await expect.poll(() => api.serviceListHits).toBeGreaterThan(hitsBeforeRefresh);
});

test("handles instance bulk actions through the same command API", async ({ page }) => {
  const api = await installApiMocks(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Instances" }).click();

  await page.locator("thead input[type='checkbox']").check();
  await expect(page.getByText("2 instances selected")).toBeVisible();
  await page.getByRole("button", { name: "Restart Selected" }).click();
  await expect(page.getByText("Restart command accepted for [111111].")).toBeVisible();
  await expect(page.getByText("Restart command accepted for [222222].")).toBeVisible();

  await page.locator("thead input[type='checkbox']").check();
  await page.getByRole("button", { name: "Stop Selected" }).click();
  await expect(page.getByText("Shutdown command accepted for [111111].")).toBeVisible();
  await expect(page.getByText("Start is not available for offline instances through the current API.")).toBeVisible();

  expect(api.commands.map((command) => [command.path, (command.body as { kind: string }).kind])).toEqual([
    ["/api/v1/instances/11111111-1111-1111-1111-111111111111/commands", "restart"],
    ["/api/v1/instances/22222222-2222-2222-2222-222222222222/commands", "restart"],
    ["/api/v1/instances/11111111-1111-1111-1111-111111111111/commands", "shutdown"],
  ]);
});

test("redirects to login when the API requires authentication", async ({ page }) => {
  await page.route("**/api/v1/services?**", async (route) => {
    await route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ detail: "authentication required" }),
    });
  });

  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
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
  });

  await page.goto("/");

  await expect(page).toHaveURL(/\/login\?next=/);
  await expect(page.getByRole("heading", { name: "Sign in to continue" })).toBeVisible();
});

test("submits console login and returns to a sanitized next path", async ({ page }) => {
  const loginRequests: unknown[] = [];
  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
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
  });
  await page.route("**/api/v1/auth/login", async (route) => {
    loginRequests.push(route.request().postDataJSON());
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        auth_configured: true,
        bootstrap_required: false,
        authenticated: true,
        username: "operator",
        role: "operator",
        roles: ["operator"],
      }),
    });
  });

  await page.goto("/login?next=%2Fservices%3Ftab%3Dinstances");
  await page.getByLabel("Username").fill("operator");
  await page.getByLabel("Password").fill("secret-password");
  await page.getByRole("button", { name: "Sign in" }).click();

  await expect(page).toHaveURL(/\/services\?tab=instances$/);
  expect(loginRequests).toEqual([{ username: "operator", password: "secret-password" }]);
});

test("shows local bootstrap guidance instead of a login form", async ({ page }) => {
  await page.route("**/api/v1/auth/session", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        auth_configured: true,
        bootstrap_required: true,
        authenticated: false,
        username: null,
        role: null,
        roles: [],
      }),
    });
  });

  await page.goto("/login?next=https://evil.example");

  await expect(page.getByText("Local admin bootstrap is required before console login.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Sign in" })).toHaveCount(0);
});

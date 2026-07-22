import { expect, test, type Page } from "@playwright/test";

const ISO_NOW = "2026-07-16T08:00:00Z";
const LOOKBACK_MINUTES = 15;
const RESOURCE_CATALOG_RESPONSE = { resources: [] };

const billingService = {
  name: "billing-sync",
  environment: "prod",
  description: "Synchronizes billing records into downstream systems.",
  latest_deployment_version: "2026.7.16",
  service_status: "online",
  latest_topology_hash: "topo-billing",
  latest_sync_at: ISO_NOW,
  instance_count: 2,
  online_instance_count: 2,
  last_seen_at: ISO_NOW,
  source_kinds: ["redis_stream"],
  task_count: 2,
  failing_task_count: 1,
  view_status: "running",
  success_rate: 99.17,
  throughput_per_min: 48,
  error_count: 1,
  uptime_reference_at: ISO_NOW,
  online_task_count: 1,
  standby_instance_count: 0,
  created_at: ISO_NOW,
  updated_at: ISO_NOW,
};

const auditService = {
  name: "audit-sync",
  environment: "staging",
  description: "Archives audit events for compliance review.",
  latest_deployment_version: "2026.7.15",
  service_status: "offline",
  latest_topology_hash: "topo-audit",
  latest_sync_at: ISO_NOW,
  instance_count: 1,
  online_instance_count: 0,
  last_seen_at: ISO_NOW,
  source_kinds: ["schedule"],
  task_count: 1,
  failing_task_count: 1,
  view_status: "stopped",
  success_rate: 0,
  throughput_per_min: 0,
  error_count: 1,
  uptime_reference_at: ISO_NOW,
  online_task_count: 0,
  standby_instance_count: 1,
  created_at: ISO_NOW,
  updated_at: ISO_NOW,
};

const eventCounts = {
  started: 1,
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
    level: "info",
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
    pause_requested: false,
    supported_commands: ["pause_task", "resume_task", "restart_task", "run_task_once"],
    event_counts: { ...eventCounts, retried: 5, succeeded: 590 },
    view_status: "running",
    success_rate: 99.15,
    throughput_per_min: 40,
    error_count: 0,
    retry_attempts: 3,
    source_label: "orders.v1",
    sink_label: "ledger-api",
    config_yaml: "task_config:\n  name: orders_to_ledger\n  source: redis_stream\n  sink: ledger-api",
    uptime_reference_at: ISO_NOW,
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
    view_status: "running",
    created_at: ISO_NOW,
    updated_at: ISO_NOW,
    ...overrides,
  };
}

async function installApiMocks(page: Page) {
  const commands: Array<{ method: string; path: string; query: Record<string, string>; body: unknown }> = [];
  const commandResults: Array<{ command_id: string; kind: string; status: string; updated_at: string }> = [];
  let serviceListHits = 0;
  const pauseRequestedByTask = new Map<string, boolean>();

  const serviceData = {
    "billing-sync": {
      service: billingService,
      dashboard: {
        service: billingService,
        lookback_minutes: LOOKBACK_MINUTES,
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
            level: "error",
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
          throughput_per_min: 8,
          source_label: "*/5 * * * *",
          sink_label: "invoice-api",
          config_yaml: "task_config:\n  name: invoice_retry\n  source: schedule\n  sink: invoice-api",
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
          view_status: "failed",
        }),
      ],
    },
    "audit-sync": {
      service: auditService,
      dashboard: {
        service: auditService,
        lookback_minutes: LOOKBACK_MINUTES,
        lookback_started_at: ISO_NOW,
        task_count: 1,
        failing_task_count: 1,
        recent_events: [taskEvent({ event_id: "evt-audit", task_name: "audit_archive", kind: "retried", level: "warn" })],
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
          pause_requested: null,
          view_status: "offline",
          success_rate: 0,
          throughput_per_min: 0,
          error_count: 1,
          source_label: "0 2 * * *",
          sink_label: "archive-api",
          config_yaml: "task_config:\n  name: audit_archive\n  source: schedule\n  sink: archive-api",
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
          view_status: "stopped",
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
      if (path === "/api/v1/auth/logout") {
        commands.push({
          method: request.method(),
          path,
          query,
          body: null,
        });
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
        return;
      }

      const body = request.postDataJSON() as { kind?: string; args?: Record<string, unknown> } | null;
      commands.push({
        method: request.method(),
        path,
        query,
        body,
      });
      const commandId = `cmd-${commands.length.toString().padStart(4, "0")}`;
      const taskCommandMatch = path.match(/^\/api\/v1\/services\/([^/]+)\/tasks\/([^/]+)\/commands$/);
      if (taskCommandMatch && body?.kind) {
        const serviceName = decodeURIComponent(taskCommandMatch[1]);
        const taskName = decodeURIComponent(taskCommandMatch[2]);
        const key = `${serviceName}:${query.environment ?? ""}:${taskName}`;
        if (body.kind === "pause_task") pauseRequestedByTask.set(key, true);
        if (body.kind === "resume_task") pauseRequestedByTask.set(key, false);
      }
      if (body?.kind) {
        commandResults.unshift({
          command_id: commandId,
          kind: body.kind,
          status: body.kind === "restart_task" ? "succeeded" : "dispatched",
          updated_at: ISO_NOW,
        });
      }
      await route.fulfill({
        status: 202,
        contentType: "application/json",
        body: JSON.stringify({
          kind: body?.kind ?? "restart",
          target_mode: "all_online",
          offline_behavior: "skip",
          noop_reason_code: null,
          noop_reason_message: null,
          counts: { dispatched: 1, queued: 0, skipped: 0, rejected: 0, total: 1 },
          dispatched: [
            {
              instance_id: "11111111-1111-1111-1111-111111111111",
              node_name: "worker-a",
              connectivity: "online",
              session_id: "session-a",
              command_id: commandId,
              outcome: "dispatched",
              reason_code: null,
              reason_message: null,
            },
          ],
          queued: [],
          skipped: [],
          rejected: [],
        }),
      });
      return;
    }

    if (path === "/api/v1/auth/session") {
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
      return;
    }

    if (path === "/api/v1/resource-catalog") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(RESOURCE_CATALOG_RESPONSE),
      });
      return;
    }

    if (path === "/api/v1/events") {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              ...taskEvent({ event_id: "evt-overview", service_name: "billing-sync", environment: "prod" }),
              service_name: "billing-sync",
              environment: "prod",
            },
          ],
          total: 1,
          limit: 20,
          offset: 0,
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
          summary: {
            total_services: 2,
            online_services: 1,
            attention_services: 0,
            offline_services: 1,
            ready_services: 1,
            total_instances: 3,
            online_instances: 2,
            total_tasks: 3,
            failing_tasks: 2,
          },
        }),
      });
      return;
    }

    const serviceCommandsMatch = path.match(/^\/api\/v1\/services\/([^/]+)\/commands$/);
    if (serviceCommandsMatch) {
      const kind = query.kind;
      const items = kind ? commandResults.filter((command) => command.kind === kind) : commandResults;
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items,
          total: items.length,
          limit: 100,
          offset: 0,
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
          lookback_minutes: LOOKBACK_MINUTES,
          lookback_started_at: ISO_NOW,
          summary: task,
          task_control: {
            task_name: taskName,
            instances: [
              {
                instance_id: "11111111-1111-1111-1111-111111111111",
                pause_requested: pauseRequestedByTask.get(`${serviceName}:${query.environment ?? ""}:${taskName}`) ?? false,
                paused: pauseRequestedByTask.get(`${serviceName}:${query.environment ?? ""}:${taskName}`) ?? false,
              },
            ],
          },
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
          ...(resource === "tasks" ? { lookback_minutes: LOOKBACK_MINUTES, lookback_started_at: ISO_NOW } : {}),
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

  // The landing view is the services list; open a service to reach its detail.
  await expect(page.getByText("Synchronizes billing records into downstream systems.")).toBeVisible();
  await page.getByText("billing-sync").first().click();
  await expect(page.getByRole("heading", { name: "billing-sync / prod" })).toBeVisible();
  await page.screenshot({ path: "/tmp/onestep-plane-tablet.png", fullPage: true });
  await expect(page.getByText("billing-sync").first()).toBeVisible();
  await expect(page.getByText("Synchronizes billing records into downstream systems.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Restart All" })).toBeVisible();
});

test("shows support links in nav bottom and signs out", async ({ page }) => {
  const api = await installApiMocks(page);
  await page.goto("/");

  const docsLink = page.getByRole("link", { name: "Docs" });
  await expect(docsLink).toHaveAttribute("href", "https://onestep.code05.com/");
  await expect(docsLink).toHaveAttribute("target", "_blank");

  const githubLink = page.getByRole("link", { name: "GitHub" });
  await expect(githubLink).toHaveAttribute("href", "https://github.com/mic1on/onestep");
  await expect(githubLink).toHaveAttribute("target", "_blank");

  await page.getByRole("button", { name: "Sign out" }).click();

  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByRole("heading", { name: "Sign in to continue" })).toBeVisible();
  expect(api.commands.some((command) => command.path === "/api/v1/auth/logout")).toBe(true);
});

test("does not render demo services while the initial API load is pending", async ({ page }) => {
  await installApiMocks(page);
  let releaseApi: () => void = () => {};
  const apiPending = new Promise<void>((resolve) => {
    releaseApi = resolve;
  });

  await page.route((url) => url.pathname === "/api/v1/services", async (route) => {
    await apiPending;
    await route.fallback();
  });

  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Connecting to API" })).toBeVisible();
  await expect(page.getByTestId("control-plane-skeleton")).toBeVisible();
  await expect(page.getByText("user-auth-service")).toHaveCount(0);
  await expect(page.getByText("Ingest-Logs")).toHaveCount(0);

  releaseApi();
  await expect(page.getByText("billing-sync").first()).toBeVisible();
  await expect(page.getByTestId("control-plane-skeleton")).toHaveCount(0);
});

test("preserves service content during a background refresh", async ({ page }) => {
  await installApiMocks(page);
  let delayNextServiceList = false;
  let releaseRefresh: () => void = () => {};
  const refreshPending = new Promise<void>((resolve) => {
    releaseRefresh = resolve;
  });

  await page.route((url) => url.pathname === "/api/v1/services", async (route) => {
    if (delayNextServiceList) {
      delayNextServiceList = false;
      await refreshPending;
    }
    await route.fallback();
  });

  await page.goto("/");
  await expect(page.getByText("billing-sync").first()).toBeVisible();

  delayNextServiceList = true;
  await page.getByRole("button", { name: "Refresh" }).click();

  await expect(page.getByRole("button", { name: "Refreshing" })).toHaveAttribute("aria-busy", "true");
  await expect(page.getByText("billing-sync").first()).toBeVisible();
  await expect(page.getByTestId("control-plane-skeleton")).toHaveCount(0);

  releaseRefresh();
  await expect(page.getByText("Control plane data refreshed from API.")).toBeVisible();
});

test("restores focus after dismissing menus and the manual-run dialog", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await expect(page.getByText("billing-sync").first()).toBeVisible();

  const environmentTrigger = page.getByRole("button", { name: /Environment/ });
  await environmentTrigger.click();
  await expect(page.getByRole("listbox")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(environmentTrigger).toBeFocused();

  await page.getByText("billing-sync").first().click();
  await page.getByText("orders_to_ledger").first().click();
  const runOnceButton = page.getByRole("button", { name: "Run once" });
  await runOnceButton.click();

  await expect(page.getByRole("dialog", { name: "Run task once" })).toBeVisible();
  await expect(page.getByLabel("JSON payload")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "Run task once" })).toHaveCount(0);
  await expect(runOnceButton).toBeFocused();
});

test("reduces topology motion when the user prefers reduced motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await installApiMocks(page);
  await page.goto("/");
  await page.getByText("billing-sync").first().click();
  await page.getByText("orders_to_ledger").first().click();

  const duration = await page.getByTestId("topology-flow-diagram").evaluate((element) =>
    getComputedStyle(element.querySelector('[data-testid="topology-flow-packet"]')!).animationDuration,
  );
  const durationMs = duration.endsWith("ms") ? Number.parseFloat(duration) : Number.parseFloat(duration) * 1000;
  expect(durationMs).toBeCloseTo(0.01, 5);
});

test("keeps the services view usable without page overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await installApiMocks(page);
  await page.goto("/");

  await expect(page.getByRole("heading", { name: "Service Directory" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Services" })).toBeVisible();
  const hasPageOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth > document.documentElement.clientWidth,
  );
  expect(hasPageOverflow).toBe(false);
});

test("loads API-backed service, task, instance, topology, config, and log views", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");

  await expect(page.getByText("API connected")).toBeVisible();
  // The services list shows all registered services.
  await expect(page.getByText("billing-sync").first()).toBeVisible();

  // Open the billing-sync service to reach its detail (tasks/instances/topology).
  await page.getByText("billing-sync").first().click();
  await expect(page.getByRole("heading", { name: "billing-sync / prod" })).toBeVisible();
  await expect(page.getByText("orders_to_ledger").first()).toBeVisible();
  await expect(page.getByText("redis_stream")).toBeVisible();
  await expect(page.getByText("ledger-api")).toBeVisible();

  await page.getByText("orders_to_ledger").first().click();
  await expect(page.getByRole("heading", { name: "orders_to_ledger" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Task Metrics" })).toBeVisible();
  const taskMetricsLookback = page.getByRole("group", { name: "Task Metrics Lookback minutes" });
  await expect(taskMetricsLookback.getByRole("button", { name: "15m", pressed: true })).toBeVisible();
  await expect(page.getByText("1020")).toBeVisible();
  await expect(page.getByRole("button", { name: "Failures" })).toBeVisible();

  await page.getByLabel("Breadcrumb").getByRole("button", { name: "Tasks" }).click();
  await expect(page).toHaveURL(/\/services\/billing-sync%3Aprod$/);
  await expect(page.getByText("orders_to_ledger").first()).toBeVisible();

  await page.getByText("orders_to_ledger").first().click();
  await page.getByLabel("Breadcrumb").getByRole("button", { name: "billing-sync / prod" }).click();
  await expect(page).toHaveURL(/\/services\/billing-sync%3Aprod$/);
  await page.getByText("orders_to_ledger").first().click();

  await page.getByRole("button", { name: "redis_stream orders.v1" }).click();
  await expect(page.getByText("redis_stream Source")).toBeVisible();
  await page.getByRole("button", { name: "orders_to_ledger Task Processing" }).click();
  await expect(page.getByText("orders_to_ledger Task")).toBeVisible();
  await page.getByRole("button", { name: "http_sink ledger-api" }).click();
  await expect(page.getByText("http_sink Sink")).toBeVisible();

  await expect(page.getByText("Active Configuration")).toBeVisible();
  await expect(page.getByText("source: redis_stream")).toBeVisible();

  await page.goto("/services/billing-sync%3Aprod?tab=instances");
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

  await page.goto("/services/billing-sync%3Aprod?tab=logs");
  await page.getByRole("button", { name: "Logs" }).click();
  await expect(page.getByText("processed order batch")).toBeVisible();
  await page.getByRole("button", { name: "Clear stream" }).click();
  await expect(page.getByText("processed order batch")).toHaveCount(0);

  await expect(page.getByRole("button", { name: "Configuration" })).toHaveCount(0);
  await expect(page.getByText("replication_factor")).toHaveCount(0);
});

test("dispatches service, task, and instance commands to the control-plane API", async ({ page }) => {
  const api = await installApiMocks(page);
  await page.goto("/");
  await expect(page.getByText("API connected")).toBeVisible();

  // Open the service from the list to reach its detail view.
  await page.getByText("billing-sync").first().click();

  await page.getByRole("button", { name: "Restart All" }).click();
  await expect(page.getByText("Restart command accepted for billing-sync / prod.")).toBeVisible();

  await page.getByRole("button", { name: "Deploy Update" }).click();
  await expect(page.getByText("Sync command accepted for billing-sync / prod.")).toBeVisible();

  await page.getByText("orders_to_ledger").first().click();
  await page.getByRole("button", { name: "Restart" }).click();
  await expect(page.getByText("orders_to_ledger restart sequence accepted.")).toBeVisible();

  await page.getByRole("button", { name: "Pause" }).click();
  await expect(page.getByText("orders_to_ledger command accepted.")).toBeVisible();

  await page.goto("/services/billing-sync%3Aprod?tab=instances");
  await page.getByRole("button", { name: "Instances" }).click();
  await page.getByTitle("Stop Instance").first().click();
  await expect(page.getByText("Shutdown command accepted for [111111].")).toBeVisible();
  await page.getByTitle("Restart Instance").first().click();
  await expect(page.getByText("Restart command accepted for [111111].")).toBeVisible();

  expect(api.commands.map((command) => [command.path, (command.body as { kind: string }).kind])).toEqual([
    ["/api/v1/services/billing-sync/commands", "restart"],
    ["/api/v1/services/billing-sync/commands", "sync_now"],
    ["/api/v1/services/billing-sync/tasks/orders_to_ledger/commands", "restart_task"],
    ["/api/v1/services/billing-sync/tasks/orders_to_ledger/commands", "pause_task"],
    ["/api/v1/instances/11111111-1111-1111-1111-111111111111/commands", "shutdown"],
    ["/api/v1/instances/11111111-1111-1111-1111-111111111111/commands", "restart"],
  ]);
  expect(api.commands[0].query).toEqual({ environment: "prod" });
  expect(api.commands[2].query).toEqual({ environment: "prod" });
  expect(api.commands[4].query).toEqual({});
});

test("switches service context and refreshes from the selected service endpoints", async ({ page }) => {
  const api = await installApiMocks(page);
  await page.goto("/");
  // Open a service to land in the detail view first.
  await page.getByText("billing-sync").first().click();
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
  // Open the service to reach its detail view (Instances tab lives here).
  await page.getByText("billing-sync").first().click();
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

  await page.route("**/api/v1/resource-catalog", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(RESOURCE_CATALOG_RESPONSE),
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

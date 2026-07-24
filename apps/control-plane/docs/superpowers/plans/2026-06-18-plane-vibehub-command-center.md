# Plane Vibehub Command Center Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first working Vibehub Command Center slice: default `/` route, grouped shell navigation, frontend-derived attention queue, and focused tests/build verification.

**Architecture:** Keep the first implementation frontend-only. Add a pure attention derivation module under `features/command-center`, render it from a new `CommandCenterPage`, and wire the page into the existing React Router/AppShell. Styling lives in the existing Vibehub stylesheet layer so current API clients, reporter contracts, WebSocket behavior, and command mutations stay unchanged.

**Tech Stack:** React 19, React Router 7, TanStack Query, TypeScript, Vitest, Testing Library, Vite, CSS modules via global stylesheet conventions already used by the app.

---

## File Structure

- Create `frontend/src/features/command-center/attention.ts`
  - Pure functions and types for converting existing list/query data into Command Center summary stats and attention items.
- Create `frontend/src/features/command-center/attention.test.ts`
  - Unit tests for service, agent, deployment, worker, connector, and stream-derived attention.
- Create `frontend/src/pages/command-center/CommandCenterPage.tsx`
  - React page that calls existing query hooks, uses the derivation helper, and renders the Command Center.
- Create `frontend/src/pages/command-center/CommandCenterPage.test.tsx`
  - Component tests for rendering summary/queue states without touching network.
- Modify `frontend/src/app/router.tsx`
  - Make `/` render `CommandCenterPage` instead of redirecting to `/services`.
- Modify `frontend/src/components/layout/AppShell.tsx`
  - Regroup navigation under Now / Deploy / Build / Admin and add Command Center link.
- Modify `frontend/src/components/layout/AppShell.test.tsx`
  - Update shell expectations for grouped navigation and permission-gated notifications.
- Modify `frontend/src/pages/login/LoginPage.tsx`
  - Change safe post-login fallback from `/services?environment=all` to `/`.
- Modify `frontend/src/lib/i18n.ts`
  - Add English and Chinese strings for Command Center and shell nav group labels.
- Modify `frontend/src/styles/vibe-reference-console.css`
  - Add Command Center and grouped shell styles, including responsive behavior and reduced-motion fallback if missing.

Do not modify backend code, query client APIs, reporter payloads, WebSocket protocol behavior, or command mutation semantics in this plan.

---

### Task 1: Attention Derivation Model

**Files:**
- Create: `frontend/src/features/command-center/attention.ts`
- Test: `frontend/src/features/command-center/attention.test.ts`

- [ ] **Step 1: Write failing tests for attention derivation**

Create `frontend/src/features/command-center/attention.test.ts`:

```ts
import { describe, expect, it } from "vitest";

import type {
  ConnectorSummary,
  ServiceListResponse,
  WorkerAgentListResponse,
  WorkerDeploymentListResponse,
  WorkerListResponse,
} from "../../lib/api/types";
import { buildCommandCenterModel } from "./attention";

const now = "2026-06-18T12:00:00.000Z";

function servicesResponse(): ServiceListResponse {
  return {
    items: [
      {
        name: "billing-sync",
        environment: "prod",
        latest_deployment_version: "2026.06.18",
        service_status: "attention",
        latest_topology_hash: "hash-a",
        latest_sync_at: now,
        instance_count: 4,
        online_instance_count: 2,
        last_seen_at: now,
        source_kinds: ["redis_stream"],
        task_count: 3,
        created_at: now,
        updated_at: now,
      },
      {
        name: "audit-relay",
        environment: "staging",
        latest_deployment_version: "2026.06.17",
        service_status: "online",
        latest_topology_hash: "hash-b",
        latest_sync_at: now,
        instance_count: 2,
        online_instance_count: 2,
        last_seen_at: now,
        source_kinds: ["schedule"],
        task_count: 1,
        created_at: now,
        updated_at: now,
      },
    ],
    total: 2,
    limit: 100,
    offset: 0,
    source_kind_counts: {},
    summary: {
      total_services: 2,
      online_services: 1,
      attention_services: 1,
      offline_services: 0,
      ready_services: 1,
      total_instances: 6,
      online_instances: 4,
    },
  };
}

function agentsResponse(): WorkerAgentListResponse {
  return {
    total: 2,
    limit: 100,
    offset: 0,
    items: [
      {
        worker_agent_id: "agent-online",
        display_name: "agent-online",
        status: "online",
        execution_mode: "subprocess",
        max_concurrent_deployments: 4,
        used_slots: 2,
        labels: {},
        capabilities: [],
        agent_version: "0.1.0",
        onestep_version: "1.0.0",
        python_version: "3.11",
        platform: {},
        registered_at: now,
        last_seen_at: now,
        created_at: now,
        updated_at: now,
      },
      {
        worker_agent_id: "agent-offline",
        display_name: "agent-offline",
        status: "offline",
        execution_mode: "subprocess",
        max_concurrent_deployments: 2,
        used_slots: 0,
        labels: {},
        capabilities: [],
        agent_version: "0.1.0",
        onestep_version: "1.0.0",
        python_version: "3.11",
        platform: {},
        registered_at: now,
        last_seen_at: null,
        created_at: now,
        updated_at: now,
      },
    ],
  };
}

function deploymentsResponse(): WorkerDeploymentListResponse {
  return {
    total: 2,
    limit: 100,
    offset: 0,
    items: [
      {
        deployment_id: "deploy-running",
        workflow_package_id: "package-running",
        worker_agent_id: "agent-online",
        desired_status: "running",
        observed_status: "running",
        runtime_instance_id: "runtime-1",
        execution_mode: "subprocess",
        params: {},
        env: {},
        credential_refs: [],
        package_checksum: "sha256:running",
        last_error_code: null,
        last_error_message: null,
        assigned_at: now,
        started_at: now,
        finished_at: null,
        created_by: "operator",
        created_at: now,
        updated_at: now,
      },
      {
        deployment_id: "deploy-failed",
        workflow_package_id: "package-failed",
        worker_agent_id: "agent-offline",
        desired_status: "running",
        observed_status: "failed",
        runtime_instance_id: null,
        execution_mode: "subprocess",
        params: {},
        env: {},
        credential_refs: [],
        package_checksum: "sha256:failed",
        last_error_code: "install_failed",
        last_error_message: "dependency install failed",
        assigned_at: now,
        started_at: now,
        finished_at: now,
        created_by: "operator",
        created_at: now,
        updated_at: now,
      },
    ],
  };
}

function workersResponse(): WorkerListResponse {
  return {
    total: 2,
    limit: 100,
    offset: 0,
    items: [
      {
        id: "worker-ready",
        name: "worker-ready",
        description: "ready worker",
        handler_package_id: "package-ready",
        handler_ref: "handler:handler",
        source_config: { type: "interval", connector_id: null, fields: {} },
        sink_configs: [],
        status: "ready",
        created_at: now,
        updated_at: now,
      },
      {
        id: "worker-draft",
        name: "worker-draft",
        description: "draft worker",
        handler_package_id: null,
        handler_ref: "handler:handler",
        source_config: { type: "interval", connector_id: null, fields: {} },
        sink_configs: [],
        status: "draft",
        created_at: now,
        updated_at: now,
      },
    ],
  };
}

const connectors: ConnectorSummary[] = [
  {
    id: "connector-1",
    name: "prod-mysql",
    type: "mysql",
    config: {},
    secret: {},
    created_at: now,
    updated_at: now,
  },
];

describe("buildCommandCenterModel", () => {
  it("summarizes cross-domain operational state", () => {
    const model = buildCommandCenterModel({
      services: servicesResponse(),
      agents: agentsResponse(),
      deployments: deploymentsResponse(),
      workers: workersResponse(),
      connectors,
      commandStreamPhase: "connected",
    });

    expect(model.summary.attentionCount).toBe(4);
    expect(model.summary.onlineInstancesLabel).toBe("4/6");
    expect(model.summary.activeServices).toBe(2);
    expect(model.summary.deploymentCount).toBe(2);
    expect(model.summary.agentCapacityLabel).toBe("2/6");
    expect(model.items.map((item) => item.id)).toEqual([
      "service:prod:billing-sync",
      "agent:agent-offline",
      "deployment:deploy-failed",
      "worker:worker-draft",
    ]);
  });

  it("adds a stream attention item when the command stream is stale or errored", () => {
    const model = buildCommandCenterModel({
      services: servicesResponse(),
      agents: agentsResponse(),
      deployments: deploymentsResponse(),
      workers: workersResponse(),
      connectors,
      commandStreamPhase: "stale",
    });

    expect(model.items.some((item) => item.id === "stream:commands")).toBe(true);
    expect(model.items.find((item) => item.id === "stream:commands")?.severity).toBe("warning");
  });
});
```

- [ ] **Step 2: Run the failing test**

Run:

```bash
cd frontend
pnpm test -- src/features/command-center/attention.test.ts
```

Expected: FAIL because `frontend/src/features/command-center/attention.ts` does not exist.

- [ ] **Step 3: Implement the pure model builder**

Create `frontend/src/features/command-center/attention.ts`:

```ts
import type {
  ConnectorSummary,
  ServiceListResponse,
  UiStreamConnectionPhase,
  WorkerAgentListResponse,
  WorkerDeploymentListResponse,
  WorkerListResponse,
} from "../../lib/api/types";

export type CommandCenterSeverity = "critical" | "warning" | "info" | "ok";

export type CommandCenterItemKind =
  | "service"
  | "worker_agent"
  | "deployment"
  | "worker"
  | "connector"
  | "stream";

export type CommandCenterAttentionItem = {
  id: string;
  kind: CommandCenterItemKind;
  severity: CommandCenterSeverity;
  label: string;
  signal: string;
  nextActionLabel: string;
  href: string;
  updatedAt: string | null;
};

export type CommandCenterSummary = {
  attentionCount: number;
  activeServices: number;
  onlineInstancesLabel: string;
  deploymentCount: number;
  agentCapacityLabel: string;
  connectorCount: number;
};

export type CommandCenterModel = {
  summary: CommandCenterSummary;
  items: CommandCenterAttentionItem[];
};

export type BuildCommandCenterInput = {
  services?: ServiceListResponse;
  agents?: WorkerAgentListResponse;
  deployments?: WorkerDeploymentListResponse;
  workers?: WorkerListResponse;
  connectors?: ConnectorSummary[];
  commandStreamPhase?: UiStreamConnectionPhase;
};

const DEPLOYMENT_ATTENTION_STATUSES = new Set(["failed", "cancelled", "stopping"]);
const STREAM_ATTENTION_PHASES = new Set<UiStreamConnectionPhase>(["stale", "error", "reconnecting"]);

export function buildCommandCenterModel(input: BuildCommandCenterInput): CommandCenterModel {
  const items: CommandCenterAttentionItem[] = [
    ...buildServiceItems(input.services),
    ...buildAgentItems(input.agents),
    ...buildDeploymentItems(input.deployments),
    ...buildWorkerItems(input.workers),
    ...buildStreamItems(input.commandStreamPhase),
  ];

  return {
    summary: {
      attentionCount: items.length,
      activeServices: input.services?.summary.total_services ?? input.services?.items.length ?? 0,
      onlineInstancesLabel: `${input.services?.summary.online_instances ?? 0}/${input.services?.summary.total_instances ?? 0}`,
      deploymentCount: input.deployments?.total ?? input.deployments?.items.length ?? 0,
      agentCapacityLabel: buildAgentCapacityLabel(input.agents),
      connectorCount: input.connectors?.length ?? 0,
    },
    items,
  };
}

function buildServiceItems(services?: ServiceListResponse): CommandCenterAttentionItem[] {
  if (!services) return [];

  return services.items
    .filter((service) => service.service_status === "attention" || service.online_instance_count < service.instance_count)
    .map((service) => ({
      id: `service:${service.environment}:${service.name}`,
      kind: "service",
      severity: service.online_instance_count === 0 ? "critical" : "warning",
      label: service.name,
      signal: `${service.online_instance_count}/${service.instance_count} instances online`,
      nextActionLabel: "Open service",
      href: `/services/${encodeURIComponent(service.name)}?environment=${service.environment}&lookback_minutes=60`,
      updatedAt: service.last_seen_at,
    }));
}

function buildAgentItems(agents?: WorkerAgentListResponse): CommandCenterAttentionItem[] {
  if (!agents) return [];

  return agents.items
    .filter((agent) => agent.status !== "online")
    .map((agent) => ({
      id: `agent:${agent.worker_agent_id}`,
      kind: "worker_agent",
      severity: "warning",
      label: agent.display_name,
      signal: `agent is ${agent.status}`,
      nextActionLabel: "Inspect agent",
      href: `/agents/${encodeURIComponent(agent.worker_agent_id)}`,
      updatedAt: agent.last_seen_at,
    }));
}

function buildDeploymentItems(deployments?: WorkerDeploymentListResponse): CommandCenterAttentionItem[] {
  if (!deployments) return [];

  return deployments.items
    .filter((deployment) => DEPLOYMENT_ATTENTION_STATUSES.has(deployment.observed_status))
    .map((deployment) => ({
      id: `deployment:${deployment.deployment_id}`,
      kind: "deployment",
      severity: deployment.observed_status === "failed" ? "critical" : "warning",
      label: deployment.workflow_package_id,
      signal: deployment.last_error_message ?? `deployment is ${deployment.observed_status}`,
      nextActionLabel: "Watch events",
      href: `/agents/${encodeURIComponent(deployment.worker_agent_id)}/deployments/${encodeURIComponent(deployment.deployment_id)}/events`,
      updatedAt: deployment.updated_at,
    }));
}

function buildWorkerItems(workers?: WorkerListResponse): CommandCenterAttentionItem[] {
  if (!workers) return [];

  return workers.items
    .filter((worker) => worker.status !== "ready")
    .map((worker) => ({
      id: `worker:${worker.id}`,
      kind: "worker",
      severity: "info",
      label: worker.name,
      signal: `worker is ${worker.status}`,
      nextActionLabel: "Open worker",
      href: `/workers/${encodeURIComponent(worker.id)}`,
      updatedAt: worker.updated_at,
    }));
}

function buildStreamItems(phase?: UiStreamConnectionPhase): CommandCenterAttentionItem[] {
  if (!phase || !STREAM_ATTENTION_PHASES.has(phase)) return [];

  return [
    {
      id: "stream:commands",
      kind: "stream",
      severity: phase === "error" ? "critical" : "warning",
      label: "Command stream",
      signal: `stream is ${phase}`,
      nextActionLabel: "Review live updates",
      href: "/services?environment=all",
      updatedAt: null,
    },
  ];
}

function buildAgentCapacityLabel(agents?: WorkerAgentListResponse) {
  if (!agents) return "0/0";
  const used = agents.items.reduce((total, agent) => total + agent.used_slots, 0);
  const total = agents.items.reduce((sum, agent) => sum + agent.max_concurrent_deployments, 0);
  return `${used}/${total}`;
}
```

- [ ] **Step 4: Run the attention tests**

Run:

```bash
cd frontend
pnpm test -- src/features/command-center/attention.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/features/command-center/attention.ts frontend/src/features/command-center/attention.test.ts
git commit -m "feat: derive command center attention model"
```

---

### Task 2: Command Center Page

**Files:**
- Create: `frontend/src/pages/command-center/CommandCenterPage.tsx`
- Create: `frontend/src/pages/command-center/CommandCenterPage.test.tsx`
- Modify: `frontend/src/lib/i18n.ts`

- [ ] **Step 1: Write a failing component test**

Create `frontend/src/pages/command-center/CommandCenterPage.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CommandCenterPage } from "./CommandCenterPage";

const mockUseServicesQuery = vi.fn();
const mockUseWorkerAgentsQuery = vi.fn();
const mockUseWorkerDeploymentsQuery = vi.fn();
const mockUseWorkersQuery = vi.fn();
const mockUseConnectorsQuery = vi.fn();
const mockUseCommandStreamStatus = vi.fn();

vi.mock("../../features/services/queries", () => ({
  useServicesQuery: () => mockUseServicesQuery(),
}));

vi.mock("../../features/worker-agents/queries", () => ({
  useWorkerAgentsQuery: () => mockUseWorkerAgentsQuery(),
  useWorkerDeploymentsQuery: () => mockUseWorkerDeploymentsQuery(),
}));

vi.mock("../../features/workers/queries", () => ({
  useWorkersQuery: () => mockUseWorkersQuery(),
}));

vi.mock("../../features/connectors/queries", () => ({
  useConnectorsQuery: () => mockUseConnectorsQuery(),
}));

vi.mock("../../features/commands/useCommandStream", () => ({
  useCommandStreamStatus: () => mockUseCommandStreamStatus(),
}));

const now = "2026-06-18T12:00:00.000Z";

describe("CommandCenterPage", () => {
  afterEach(() => {
    mockUseServicesQuery.mockReset();
    mockUseWorkerAgentsQuery.mockReset();
    mockUseWorkerDeploymentsQuery.mockReset();
    mockUseWorkersQuery.mockReset();
    mockUseConnectorsQuery.mockReset();
    mockUseCommandStreamStatus.mockReset();
  });

  it("renders the command center summary and attention queue", async () => {
    mockUseServicesQuery.mockReturnValue({
      isPending: false,
      error: null,
      data: {
        items: [
          {
            name: "billing-sync",
            environment: "prod",
            latest_deployment_version: "2026.06.18",
            service_status: "attention",
            latest_topology_hash: "hash",
            latest_sync_at: now,
            instance_count: 4,
            online_instance_count: 2,
            last_seen_at: now,
            source_kinds: [],
            task_count: 1,
            created_at: now,
            updated_at: now,
          },
        ],
        total: 1,
        limit: 100,
        offset: 0,
        source_kind_counts: {},
        summary: {
          total_services: 1,
          online_services: 0,
          attention_services: 1,
          offline_services: 0,
          ready_services: 0,
          total_instances: 4,
          online_instances: 2,
        },
      },
    });
    mockUseWorkerAgentsQuery.mockReturnValue({ isPending: false, error: null, data: { items: [], total: 0, limit: 100, offset: 0 } });
    mockUseWorkerDeploymentsQuery.mockReturnValue({ isPending: false, error: null, data: { items: [], total: 0, limit: 100, offset: 0 } });
    mockUseWorkersQuery.mockReturnValue({ isPending: false, error: null, data: { items: [], total: 0, limit: 100, offset: 0 } });
    mockUseConnectorsQuery.mockReturnValue({ isPending: false, error: null, data: { items: [] } });
    mockUseCommandStreamStatus.mockReturnValue("connected");

    render(
      <MemoryRouter>
        <CommandCenterPage />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "What needs attention now" })).toBeInTheDocument();
    expect(screen.getByText("Attention")).toBeInTheDocument();
    expect(screen.getByText("2/4")).toBeInTheDocument();
    expect(screen.getByText("billing-sync")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open service/i })).toHaveAttribute(
      "href",
      "/services/billing-sync?environment=prod&lookback_minutes=60",
    );
  });
});
```

- [ ] **Step 2: Run the failing component test**

Run:

```bash
cd frontend
pnpm test -- src/pages/command-center/CommandCenterPage.test.tsx
```

Expected: FAIL because the page file does not exist.

- [ ] **Step 3: Add i18n strings**

Modify `frontend/src/lib/i18n.ts` by adding these keys to both English and
Chinese resource objects. Use the existing resource structure and keep adjacent
`app` keys together:

```ts
commandCenterNav: "Command Center",
navGroupNow: "Now",
navGroupDeploy: "Deploy",
navGroupBuild: "Build",
navGroupAdmin: "Admin",
```

Add a new `commandCenter` namespace in English:

```ts
commandCenter: {
  eyebrow: "Command Center / 动作中枢",
  title: "What needs attention now",
  subtitle: "A live triage board for services, agents, deployments, build readiness, and command stream freshness.",
  scopeLabel: "Scope",
  scopeAll: "All environments",
  searchLabel: "Search",
  searchPlaceholder: "Search attention items",
  summaryAttention: "Attention",
  summaryInstances: "Online instances",
  summaryServices: "Active services",
  summaryDeployments: "Deployments",
  summaryCapacity: "Agent capacity",
  queueTitle: "Attention queue",
  queueSubtitle: "Cross-domain signals derived from existing control-plane data.",
  actionsTitle: "Next actions",
  actionsSubtitle: "Open the right surface without bypassing permissions.",
  lowerAgentsTitle: "Agent capacity",
  lowerBuildTitle: "Build readiness",
  lowerNotificationsTitle: "Notification coverage",
  emptyTitle: "Nothing needs attention",
  emptyBody: "Services, agents, deployments, and build surfaces are not reporting actionable issues.",
  loading: "Loading command center...",
  loadErrorTitle: "Unable to load command center",
}
```

Add a matching Chinese namespace:

```ts
commandCenter: {
  eyebrow: "Command Center / 动作中枢",
  title: "当前需要处理的事项",
  subtitle: "汇总服务、代理、部署、构建就绪度和命令流新鲜度的实时处理面板。",
  scopeLabel: "范围",
  scopeAll: "全部环境",
  searchLabel: "搜索",
  searchPlaceholder: "搜索待处理事项",
  summaryAttention: "待处理",
  summaryInstances: "在线实例",
  summaryServices: "活跃服务",
  summaryDeployments: "部署",
  summaryCapacity: "代理容量",
  queueTitle: "待处理队列",
  queueSubtitle: "从现有控制平面数据推导出的跨域信号。",
  actionsTitle: "下一步动作",
  actionsSubtitle: "进入正确页面，同时保留权限与确认流程。",
  lowerAgentsTitle: "代理容量",
  lowerBuildTitle: "构建就绪",
  lowerNotificationsTitle: "通知覆盖",
  emptyTitle: "暂无需要处理的事项",
  emptyBody: "服务、代理、部署和构建页面没有报告需要处理的问题。",
  loading: "正在加载动作中枢...",
  loadErrorTitle: "无法加载动作中枢",
}
```

- [ ] **Step 4: Implement `CommandCenterPage`**

Create `frontend/src/pages/command-center/CommandCenterPage.tsx`:

```tsx
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

import { EmptyState } from "../../components/ui/EmptyState";
import { buildCommandCenterModel, type CommandCenterAttentionItem } from "../../features/command-center/attention";
import { useCommandStreamStatus } from "../../features/commands/useCommandStream";
import { useConnectorsQuery } from "../../features/connectors/queries";
import { useServicesQuery } from "../../features/services/queries";
import { useWorkerAgentsQuery, useWorkerDeploymentsQuery } from "../../features/worker-agents/queries";
import { useWorkersQuery } from "../../features/workers/queries";
import { formatRelativeTime } from "../../lib/formatters";

export function CommandCenterPage() {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const servicesQuery = useServicesQuery();
  const agentsQuery = useWorkerAgentsQuery();
  const deploymentsQuery = useWorkerDeploymentsQuery();
  const workersQuery = useWorkersQuery();
  const connectorsQuery = useConnectorsQuery();
  const commandStreamPhase = useCommandStreamStatus();

  const model = useMemo(
    () =>
      buildCommandCenterModel({
        services: servicesQuery.data,
        agents: agentsQuery.data,
        deployments: deploymentsQuery.data,
        workers: workersQuery.data,
        connectors: connectorsQuery.data?.items,
        commandStreamPhase,
      }),
    [servicesQuery.data, agentsQuery.data, deploymentsQuery.data, workersQuery.data, connectorsQuery.data, commandStreamPhase],
  );

  const filteredItems = model.items.filter((item) =>
    `${item.label} ${item.signal} ${item.kind}`.toLowerCase().includes(search.trim().toLowerCase()),
  );
  const isPending =
    servicesQuery.isPending ||
    agentsQuery.isPending ||
    deploymentsQuery.isPending ||
    workersQuery.isPending ||
    connectorsQuery.isPending;
  const firstError =
    servicesQuery.error ||
    agentsQuery.error ||
    deploymentsQuery.error ||
    workersQuery.error ||
    connectorsQuery.error;

  return (
    <div className="command-center-page">
      <header className="command-center-header">
        <div className="command-center-header-copy">
          <span className="signal-console-kicker">{t("commandCenter.eyebrow")}</span>
          <h2>{t("commandCenter.title")}</h2>
          <p>{t("commandCenter.subtitle")}</p>
        </div>
        <div className="command-center-controls">
          <label className="ref-inline-control">
            <span>{t("commandCenter.scopeLabel")}</span>
            <select value="all" disabled>
              <option value="all">{t("commandCenter.scopeAll")}</option>
            </select>
          </label>
          <label className="ref-inline-control">
            <span>{t("commandCenter.searchLabel")}</span>
            <input
              type="search"
              value={search}
              placeholder={t("commandCenter.searchPlaceholder")}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
        </div>
      </header>

      <section className="command-center-summary ref-summary-strip">
        <SummaryChip label={t("commandCenter.summaryAttention")} tone="danger" value={String(model.summary.attentionCount)} />
        <SummaryChip label={t("commandCenter.summaryInstances")} tone="success" value={model.summary.onlineInstancesLabel} />
        <SummaryChip label={t("commandCenter.summaryServices")} value={String(model.summary.activeServices)} />
        <SummaryChip label={t("commandCenter.summaryDeployments")} value={String(model.summary.deploymentCount)} />
        <SummaryChip label={t("commandCenter.summaryCapacity")} value={model.summary.agentCapacityLabel} />
      </section>

      {firstError ? <EmptyState title={t("commandCenter.loadErrorTitle")} body={String(firstError)} /> : null}
      {isPending ? <div className="loading-block">{t("commandCenter.loading")}</div> : null}

      {!isPending && !firstError ? (
        <div className="command-center-workbench">
          <section className="command-center-panel command-center-queue">
            <header className="panel-header">
              <div>
                <h3>{t("commandCenter.queueTitle")}</h3>
                <p>{t("commandCenter.queueSubtitle")}</p>
              </div>
            </header>
            {filteredItems.length === 0 ? (
              <EmptyState title={t("commandCenter.emptyTitle")} body={t("commandCenter.emptyBody")} />
            ) : (
              <div className="command-center-attention-list">
                {filteredItems.map((item) => (
                  <AttentionRow item={item} key={item.id} />
                ))}
              </div>
            )}
          </section>

          <section className="command-center-panel command-center-actions">
            <header className="panel-header">
              <div>
                <h3>{t("commandCenter.actionsTitle")}</h3>
                <p>{t("commandCenter.actionsSubtitle")}</p>
              </div>
            </header>
            <div className="command-center-action-list">
              {filteredItems.slice(0, 3).map((item) => (
                <Link className="command-center-action" key={`action:${item.id}`} to={item.href}>
                  <span className={`command-center-dot is-${item.severity}`} />
                  <span>
                    <strong>{item.nextActionLabel}</strong>
                    <small>{item.label}</small>
                  </span>
                </Link>
              ))}
            </div>
          </section>
        </div>
      ) : null}

      <section className="command-center-lower">
        <SignalPanel title={t("commandCenter.lowerAgentsTitle")} value={model.summary.agentCapacityLabel} />
        <SignalPanel title={t("commandCenter.lowerBuildTitle")} value={String(workersQuery.data?.items.filter((worker) => worker.status === "ready").length ?? 0)} />
        <SignalPanel title={t("commandCenter.lowerNotificationsTitle")} value={String(model.summary.connectorCount)} />
      </section>
    </div>
  );
}

function SummaryChip({ label, value, tone = "default" }: { label: string; value: string; tone?: "default" | "success" | "danger" }) {
  return (
    <article className={`ref-summary-chip ref-summary-chip-${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
    </article>
  );
}

function AttentionRow({ item }: { item: CommandCenterAttentionItem }) {
  return (
    <article className="command-center-attention-row">
      <div className="command-center-attention-main">
        <span className={`status-badge badge-${badgeTone(item.severity)}`}>{item.kind.replace("_", " ")}</span>
        <strong>{item.label}</strong>
        <p>{item.signal}</p>
      </div>
      <div className="command-center-attention-meta">
        <span>{item.updatedAt ? formatRelativeTime(item.updatedAt) : "--"}</span>
        <Link to={item.href}>{item.nextActionLabel}</Link>
      </div>
    </article>
  );
}

function SignalPanel({ title, value }: { title: string; value: string }) {
  return (
    <article className="command-center-signal-panel">
      <span>{title}</span>
      <strong>{value}</strong>
    </article>
  );
}

function badgeTone(severity: CommandCenterAttentionItem["severity"]) {
  if (severity === "critical") return "danger";
  if (severity === "warning") return "warning";
  if (severity === "ok") return "success";
  return "accent";
}
```

- [ ] **Step 5: Run component test**

Run:

```bash
cd frontend
pnpm test -- src/pages/command-center/CommandCenterPage.test.tsx
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/pages/command-center frontend/src/lib/i18n.ts
git commit -m "feat: add command center page"
```

---

### Task 3: Route Default and Shell Navigation

**Files:**
- Modify: `frontend/src/app/router.tsx`
- Modify: `frontend/src/components/layout/AppShell.tsx`
- Modify: `frontend/src/components/layout/AppShell.test.tsx`
- Modify: `frontend/src/pages/login/LoginPage.tsx`

- [ ] **Step 1: Write failing shell/router expectations**

Modify `frontend/src/components/layout/AppShell.test.tsx` so the shell render
starts at `/` and includes the Command Center child route:

```tsx
function renderShell() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
      },
    },
  });

  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<div>Command center page</div>} />
            <Route path="/services" element={<div>Services page</div>} />
          </Route>
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
```

Add this test:

```tsx
it("groups primary navigation around command center workflows", async () => {
  mockUseConsoleSessionQuery.mockReturnValue({
    isPending: false,
    error: null,
    data: {
      auth_configured: true,
      bootstrap_required: false,
      authenticated: true,
      username: "operator",
      role: "operator",
      roles: ["operator"],
    },
  });

  renderShell();

  expect(await screen.findByText("Command center page")).toBeInTheDocument();
  expect(screen.getByRole("link", { name: "Command Center" })).toBeInTheDocument();
  expect(screen.getByText("Now")).toBeInTheDocument();
  expect(screen.getByText("Deploy")).toBeInTheDocument();
  expect(screen.getByText("Build")).toBeInTheDocument();
  expect(screen.getByText("Admin")).toBeInTheDocument();
});
```

- [ ] **Step 2: Run failing shell test**

Run:

```bash
cd frontend
pnpm test -- src/components/layout/AppShell.test.tsx
```

Expected: FAIL because shell has no Command Center link or group labels yet.

- [ ] **Step 3: Wire route default**

Modify `frontend/src/app/router.tsx`:

```tsx
import { CommandCenterPage } from "../pages/command-center/CommandCenterPage";
```

Replace the current index redirect:

```tsx
{
  index: true,
  element: <CommandCenterPage />,
},
```

Keep `/services` and all existing detail routes unchanged.

- [ ] **Step 4: Update post-login fallback**

Modify `sanitizeNextPath` in `frontend/src/pages/login/LoginPage.tsx`:

```ts
function sanitizeNextPath(candidate: string | null) {
  if (!candidate || !candidate.startsWith("/") || candidate.startsWith("//")) {
    return "/";
  }
  return candidate;
}
```

- [ ] **Step 5: Update grouped shell navigation**

Modify the `<nav className="shell-nav">` body in
`frontend/src/components/layout/AppShell.tsx` to render grouped links:

```tsx
<NavGroup label={t("app.navGroupNow")}>
  <ShellNavLink to="/">{t("app.commandCenterNav")}</ShellNavLink>
  <ShellNavLink to="/services?environment=all">{t("app.servicesNav")}</ShellNavLink>
</NavGroup>
<NavGroup label={t("app.navGroupDeploy")}>
  <ShellNavLink to="/agents">{t("app.agentsNav")}</ShellNavLink>
</NavGroup>
<NavGroup label={t("app.navGroupBuild")}>
  <ShellNavLink to="/workers">{t("app.workersNav")}</ShellNavLink>
  <ShellNavLink to="/connectors">{t("app.connectorsNav")}</ShellNavLink>
</NavGroup>
{canManageNotifications ? (
  <NavGroup label={t("app.navGroupAdmin")}>
    <ShellNavLink to="/settings/notifications">{t("app.notificationsNav")}</ShellNavLink>
  </NavGroup>
) : null}
```

Add helper components below `AppShell`:

```tsx
function NavGroup({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="shell-nav-group">
      <span className="shell-nav-group-label">{label}</span>
      {children}
    </div>
  );
}

function ShellNavLink({ to, children }: { to: string; children: React.ReactNode }) {
  return (
    <NavLink className={({ isActive }) => (isActive ? "shell-nav-link active" : "shell-nav-link")} to={to}>
      {children}
    </NavLink>
  );
}
```

Update the React import:

```tsx
import type { ReactNode } from "react";
```

and use `ReactNode` in helper props.

- [ ] **Step 6: Run shell tests**

Run:

```bash
cd frontend
pnpm test -- src/components/layout/AppShell.test.tsx
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/app/router.tsx frontend/src/components/layout/AppShell.tsx frontend/src/components/layout/AppShell.test.tsx frontend/src/pages/login/LoginPage.tsx
git commit -m "feat: route console through command center"
```

---

### Task 4: Vibehub Styling for Command Center and Grouped Shell

**Files:**
- Modify: `frontend/src/styles/vibe-reference-console.css`

- [ ] **Step 1: Add Command Center CSS**

Append these styles to `frontend/src/styles/vibe-reference-console.css`:

```css
.shell-nav-group {
  display: grid;
  gap: 7px;
}

.shell-nav-group + .shell-nav-group {
  margin-top: 14px;
}

.shell-nav-group-label {
  color: rgba(255, 255, 255, 0.38);
  font-family: "IBM Plex Mono", "Space Mono", monospace;
  font-size: 10px;
  font-weight: 700;
  text-transform: uppercase;
}

.command-center-page {
  display: grid;
  gap: 16px;
  color: var(--vibe-ink);
}

.command-center-header {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(320px, 460px);
  gap: 28px;
  align-items: end;
}

.command-center-header-copy h2 {
  margin: 8px 0;
  color: var(--vibe-ink);
  font-size: 42px;
  font-weight: 700;
  line-height: 1.05;
}

.command-center-header-copy p {
  max-width: 720px;
  margin: 0;
  color: var(--vibe-ink-soft);
  font-size: 15px;
  line-height: 1.65;
}

.command-center-controls {
  display: grid;
  grid-template-columns: minmax(130px, 0.7fr) minmax(180px, 1fr);
  gap: 10px;
}

.command-center-summary.ref-summary-strip {
  grid-template-columns: repeat(5, minmax(0, 1fr));
}

.command-center-workbench {
  display: grid;
  grid-template-columns: minmax(0, 1.25fr) minmax(300px, 0.75fr);
  gap: 14px;
}

.command-center-panel,
.command-center-signal-panel {
  border: 1px solid var(--vibe-line);
  border-radius: var(--vibe-radius);
  background: var(--vibe-surface);
  box-shadow: none;
}

.command-center-attention-list,
.command-center-action-list {
  display: grid;
}

.command-center-attention-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 16px;
  align-items: center;
  min-height: 74px;
  padding: 12px 14px;
  border-top: 1px solid var(--vibe-line);
}

.command-center-attention-row:first-child {
  border-top: 0;
}

.command-center-attention-main {
  display: grid;
  gap: 5px;
  min-width: 0;
}

.command-center-attention-main strong {
  color: var(--vibe-ink);
  font-size: 14px;
}

.command-center-attention-main p,
.command-center-attention-meta span,
.command-center-action small {
  margin: 0;
  color: var(--vibe-muted);
  font-size: 12px;
}

.command-center-attention-meta {
  display: grid;
  justify-items: end;
  gap: 7px;
}

.command-center-attention-meta a,
.command-center-action {
  color: var(--vibe-accent);
  font-size: 12px;
  font-weight: 700;
  text-decoration: none;
}

.command-center-action {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  gap: 10px;
  align-items: center;
  min-height: 62px;
  padding: 11px 14px;
  border-top: 1px solid var(--vibe-line);
}

.command-center-action:first-child {
  border-top: 0;
}

.command-center-action span:last-child {
  display: grid;
  gap: 4px;
}

.command-center-action strong {
  color: var(--vibe-ink);
  font-size: 13px;
}

.command-center-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: var(--vibe-blue);
}

.command-center-dot.is-critical {
  background: var(--vibe-accent);
}

.command-center-dot.is-warning {
  background: var(--vibe-amber);
}

.command-center-dot.is-info {
  background: var(--vibe-blue);
}

.command-center-lower {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 14px;
}

.command-center-signal-panel {
  display: grid;
  gap: 10px;
  min-height: 118px;
  padding: 16px;
}

.command-center-signal-panel span {
  color: var(--vibe-muted);
  font-family: "IBM Plex Mono", "Space Mono", monospace;
  font-size: 11px;
  font-weight: 700;
  text-transform: uppercase;
}

.command-center-signal-panel strong {
  color: var(--vibe-ink);
  font-size: 32px;
  line-height: 1;
}

@media (max-width: 980px) {
  .command-center-header,
  .command-center-workbench,
  .command-center-lower {
    grid-template-columns: 1fr;
  }

  .command-center-controls,
  .command-center-summary.ref-summary-strip {
    grid-template-columns: 1fr;
  }
}
```

- [ ] **Step 2: Run the Command Center and shell tests**

Run:

```bash
cd frontend
pnpm test -- src/features/command-center/attention.test.ts src/pages/command-center/CommandCenterPage.test.tsx src/components/layout/AppShell.test.tsx
```

Expected: PASS.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/styles/vibe-reference-console.css
git commit -m "style: add command center vibehub layout"
```

---

### Task 5: Build Verification and Cleanup

**Files:**
- Modify only files needed to fix test/build failures introduced by Tasks 1-4.

- [ ] **Step 1: Run focused frontend tests**

Run:

```bash
cd frontend
pnpm test -- src/features/command-center/attention.test.ts src/pages/command-center/CommandCenterPage.test.tsx src/components/layout/AppShell.test.tsx
```

Expected: PASS.

- [ ] **Step 2: Run frontend build**

Run:

```bash
cd frontend
pnpm build
```

Expected: PASS with TypeScript and Vite build completing.

- [ ] **Step 3: Inspect final diff**

Run:

```bash
git status --short
git diff --stat
```

Expected: only intentional frontend files and this plan remain modified or
committed. `.superpowers/` may remain untracked from brainstorming and should
not be committed with implementation code.

- [ ] **Step 4: Final implementation commit if needed**

If build fixes required additional edits, commit them:

```bash
git add frontend/src
git commit -m "fix: complete command center frontend verification"
```

Expected: working tree contains only pre-existing unrelated changes or local
brainstorming artifacts.

---

## Self-Review

- Spec coverage: this plan implements the first frontend-only slice of the
  approved spec: Command Center default route, grouped IA, existing-query
  aggregation, Vibehub styling, and focused tests/build. It does not attempt the
  full second and third redesign phases for every domain page.
- Placeholder scan: no task uses placeholder or fill-in language. Code-oriented
  steps include concrete snippets and exact commands.
- Type consistency: attention item and summary types are defined in Task 1 and
  consumed by Task 2. Route and shell names match the i18n keys added in Task 2.

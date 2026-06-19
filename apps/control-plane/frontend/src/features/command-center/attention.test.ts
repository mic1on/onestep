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
    items: [
      {
        id: "worker-ready",
        name: "worker-ready",
        description: "ready worker",
        handler_package_id: "package-ready",
        handler_ref: "handler:handler",
        source_config: { type: "interval", connector_id: null, fields: {} },
        sink_configs: [],
        env: {},
        reporting_enabled: true,
        reporting_config: { mode: "platform", endpoint_url: null },
        reporting_token_configured: false,
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
        env: {},
        reporting_enabled: true,
        reporting_config: { mode: "platform", endpoint_url: null },
        reporting_token_configured: false,
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

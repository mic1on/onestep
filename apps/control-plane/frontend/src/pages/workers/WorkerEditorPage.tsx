import { useEffect, useMemo, useRef, useState } from "react";
import Editor from "@monaco-editor/react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { VibeButton } from "../../components/ui/VibeButton";
import { VibeField } from "../../components/ui/VibeField";
import { VibeInlineNotice } from "../../components/ui/VibeInlineNotice";
import { VibeModal } from "../../components/ui/VibeModal";
import { VibehubSelect } from "../../components/ui/VibehubSelect";
import { useConnectorsQuery } from "../../features/connectors/queries";
import {
  useCreateWorkflowPackageMutation,
  useWorkerAgentsQuery,
} from "../../features/worker-agents/queries";
import {
  sinkTypeSchemas,
  sinkTypeOrder,
  type SourceSinkField,
  type SourceSinkTypeSchema,
  sourceTypeOrder,
  sourceTypeSchemas,
} from "../../features/workers/catalog";
import {
  buildTreeFromPaths,
  collectDirectoryPaths,
  flattenFilePaths,
  type FileNode,
} from "../../features/workers/fileTree";
import {
  buildZipBlob,
  parseZip,
  readFileBytes,
} from "../../features/workers/zipUtils";
import {
  useCreateWorkerMutation,
  useDeployWorkerMutation,
  useDownloadWorkerPackageMutation,
  useUpdateWorkerMutation,
  useWorkerQuery,
} from "../../features/workers/queries";
import type {
  WorkerCreateRequest,
  WorkerAgentSummary,
  WorkerReportingConfig,
  WorkerSinkConfig,
  WorkerSummary,
  WorkerSourceConfig,
} from "../../lib/api/types";
import { WorkerAgentDeployCard } from "./components/WorkerAgentDeployCard";
import { WorkerConfigSection } from "./components/WorkerConfigSection";
import { WorkerEnvRow } from "./components/WorkerEnvRow";

type WorkerTab = "overview" | "config" | "code" | "deploy";

type Draft = {
  name: string;
  description: string;
  handlerRef: string;
  source: WorkerSourceConfig;
  sinks: WorkerSinkConfig[];
  env: EnvVarDraft[];
  reportingEnabled: boolean;
  reportingConfig: WorkerReportingConfig;
  reportingTokenConfigured: boolean;
  reportingToken: string;
};

type EnvVarDraft = {
  id: string;
  key: string;
  value: string;
};

type IntervalUnit = "minutes" | "seconds";
type ImmediateValue = "true" | "false" | "";

let envVarDraftSequence = 0;

function createEnvVarDraft(key = "", value = ""): EnvVarDraft {
  envVarDraftSequence += 1;
  return { id: `env-${envVarDraftSequence}`, key, value };
}

function envRecordToDraftRows(env: Record<string, string> | undefined): EnvVarDraft[] {
  return Object.entries(env ?? {}).map(([key, value]) => createEnvVarDraft(key, value));
}

function normalizeEnvRows(rows: EnvVarDraft[]): Record<string, string> {
  const env: Record<string, string> = {};
  for (const row of rows) {
    const key = row.key.trim();
    if (key) env[key] = row.value;
  }
  return env;
}

function buildWorkerConfigPayload(draft: Draft, handlerPackageId: string | null): WorkerCreateRequest {
  const payload: WorkerCreateRequest = {
    name: draft.name,
    description: draft.description,
    handler_package_id: handlerPackageId,
    handler_ref: draft.handlerRef,
    source_config: normalizeSourceConfig(draft.source),
    sink_configs: draft.sinks.map(normalizeSinkConfig),
    env: normalizeEnvRows(draft.env),
    reporting_enabled: draft.reportingEnabled,
    reporting_config:
      draft.reportingConfig.mode === "custom"
        ? draft.reportingConfig
        : { mode: "platform", endpoint_url: null },
  };
  const reportingToken = draft.reportingToken.trim();
  if (reportingToken) {
    payload.reporting_secret = { token: reportingToken };
  }
  return payload;
}

function emptyDraft(): Draft {
  return {
    name: "",
    description: "",
    handlerRef: "handler:handler",
    source: createSourceConfig("interval"),
    sinks: [],
    env: [],
    reportingEnabled: true,
    reportingConfig: { mode: "platform", endpoint_url: null },
    reportingTokenConfigured: false,
    reportingToken: "",
  };
}

function draftFromWorker(worker: WorkerSummary): Draft {
  return {
    name: worker.name,
    description: worker.description,
    handlerRef: worker.handler_ref,
    source: worker.source_config,
    sinks: worker.sink_configs,
    env: envRecordToDraftRows(worker.env),
    reportingEnabled: worker.reporting_enabled ?? true,
    reportingConfig: worker.reporting_config ?? { mode: "platform", endpoint_url: null },
    reportingTokenConfigured: worker.reporting_token_configured ?? false,
    reportingToken: "",
  };
}

function canSaveReportingDraft(draft: Draft) {
  if (!draft.reportingEnabled || draft.reportingConfig.mode !== "custom") return true;
  return Boolean(draft.reportingConfig.endpoint_url?.trim()) && (
    draft.reportingTokenConfigured || Boolean(draft.reportingToken.trim())
  );
}

function handlerFileName(handlerRef: string) {
  const [modulePath] = handlerRef.split(":");
  const moduleSegments = modulePath.split(".").filter(Boolean);
  const lastSegment = moduleSegments[moduleSegments.length - 1];
  return `${lastSegment || "handler"}.py`;
}

function handlerFunctionName(handlerRef: string) {
  const rawName = handlerRef.split(":")[1] || "handler";
  return rawName.replace(/[^\w]/g, "_") || "handler";
}

function codePreviewLines(draft: Draft) {
  const functionName = handlerFunctionName(draft.handlerRef);
  const stepName = draft.name || "new-step";
  return [
    "from __future__ import annotations",
    "",
    "from typing import Any",
    "",
    "",
    `async def ${functionName}(ctx, item) -> dict[str, Any]:`,
    `    """Handler for ${stepName}."""`,
    "    payload = item",
    "    # Add business logic here; source and sink wiring stays in step config.",
    "    return {\"ok\": True, \"payload\": payload}",
  ];
}

function defaultHandlerCode(draft: Draft) {
  return `${codePreviewLines(draft).join("\n")}\n`;
}

function handlerModulePath(handlerRef: string) {
  const [modulePath] = handlerRef.split(":");
  const cleanModulePath = modulePath
    .split(".")
    .map((segment) => segment.replace(/[^\w]/g, "_"))
    .filter(Boolean)
    .join("/");
  return `${cleanModulePath || "handler"}.py`;
}

function packageInitPaths(handlerPath: string) {
  const segments = handlerPath.split("/").slice(0, -1);
  return segments.map((_, index) => `${segments.slice(0, index + 1).join("/")}/__init__.py`);
}

function safePackageName(name: string) {
  const safe = name.toLowerCase().replace(/[^a-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
  return safe || "step";
}

function displayFileName(node: Pick<FileNode, "name" | "path">) {
  return node.path === "worker.yaml" ? "step.yaml" : node.name;
}

/**
 * Build a default single-file package from the handler ref (for the initial
 * state when no zip has been uploaded yet).
 */
function buildInitialFilesMap(handlerRef: string): Record<string, string> {
  const handlerPath = handlerModulePath(handlerRef);
  const initEntries = packageInitPaths(handlerPath);
  const map: Record<string, string> = {};
  for (const initPath of initEntries) {
    map[initPath] = "";
  }
  map[handlerPath] = defaultHandlerCode({ handlerRef } as Draft);
  return map;
}

function detectLanguage(fileName: string): string {
  if (fileName.endsWith(".py")) return "python";
  if (fileName.endsWith(".yaml") || fileName.endsWith(".yml")) return "yaml";
  if (fileName.endsWith(".json")) return "json";
  if (fileName.endsWith(".toml")) return "ini";
  if (fileName.endsWith(".md")) return "markdown";
  if (fileName.endsWith(".txt")) return "plaintext";
  if (fileName.endsWith(".sh")) return "shell";
  return "plaintext";
}

function fieldDisplayValue(value: unknown) {
  if (Array.isArray(value)) return value.map(String).join(", ");
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  if (value === null || value === undefined) return "";
  return String(value);
}

function sourceParamSortKey(name: string) {
  if (name === "minutes" || name === "seconds") return 0;
  if (name === "immediate") return 1;
  return 2;
}

function fieldInputType(field: SourceSinkField) {
  if (field.type === "password") return "password";
  if (field.type === "number") return "number";
  return "text";
}

function intervalUnitForFields(fields: Record<string, unknown>): IntervalUnit {
  return fieldDisplayValue(fields.seconds).trim() ? "seconds" : "minutes";
}

function intervalValueForFields(fields: Record<string, unknown>, unit: IntervalUnit) {
  return fieldDisplayValue(fields[unit]);
}

function immediateValueForFields(fields: Record<string, unknown>): ImmediateValue {
  const value = fieldDisplayValue(fields.immediate).trim().toLowerCase();
  if (value === "true") return "true";
  if (value === "false") return "false";
  return "";
}

function setIntervalScheduleFields(
  fields: Record<string, unknown>,
  unit: IntervalUnit,
  value: string,
) {
  const nextFields = { ...fields };
  nextFields[unit] = value;
  delete nextFields[unit === "minutes" ? "seconds" : "minutes"];
  return nextFields;
}

function setIntervalImmediateField(fields: Record<string, unknown>, value: ImmediateValue) {
  const nextFields = { ...fields };
  if (value) {
    nextFields.immediate = value;
  } else {
    delete nextFields.immediate;
  }
  return nextFields;
}

function normalizeFieldValue(field: SourceSinkField, rawValue: unknown) {
  const value = fieldDisplayValue(rawValue).trim();
  if (!value) return undefined;
  if (field.type === "list") {
    return value
      .split(",")
      .map((part) => part.trim())
      .filter(Boolean);
  }
  if (field.type === "number") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : value;
  }
  if (field.type === "json") {
    try {
      return JSON.parse(value);
    } catch {
      return value;
    }
  }
  return value;
}

function normalizeConfigFields(schema: SourceSinkTypeSchema | undefined, fields: Record<string, unknown>) {
  if (!schema) return fields;
  const normalized: Record<string, unknown> = {};
  for (const field of schema.fields) {
    const value = normalizeFieldValue(field, fields[field.name] ?? field.defaultValue);
    if (Array.isArray(value) && value.length === 0) continue;
    if (value !== undefined) normalized[field.name] = value;
  }
  return normalized;
}

function hasRequiredFields(schema: SourceSinkTypeSchema | undefined, fields: Record<string, unknown>) {
  if (!schema) return true;
  return schema.fields.every((field) => {
    if (!field.required) return true;
    const value = normalizeFieldValue(field, fields[field.name]);
    return Array.isArray(value) ? value.length > 0 : value !== undefined;
  });
}

function normalizeSourceConfig(source: WorkerSourceConfig): WorkerSourceConfig {
  return {
    ...source,
    fields: normalizeConfigFields(sourceTypeSchemas[source.type], source.fields),
  };
}

function normalizeSinkConfig(sink: WorkerSinkConfig): WorkerSinkConfig {
  const fields = normalizeConfigFields(sinkTypeSchemas[sink.type], sink.fields);
  if (sink.type === "http_sink" && isBodylessHttpMethod(fields.method)) {
    delete fields.body;
  }
  return {
    ...sink,
    fields,
  };
}

function isBodylessHttpMethod(method: unknown) {
  const normalized = String(method || "POST").trim().toUpperCase();
  return normalized === "GET" || normalized === "DELETE";
}

function shouldRenderSinkField(sink: WorkerSinkConfig, field: SourceSinkField) {
  if (sink.type === "http_sink" && field.name === "body") {
    return !isBodylessHttpMethod(sink.fields.method);
  }
  return true;
}

function defaultFieldsForSchema(schema: SourceSinkTypeSchema | undefined) {
  const fields: Record<string, unknown> = {};
  for (const field of schema?.fields ?? []) {
    if (field.defaultValue !== undefined) fields[field.name] = field.defaultValue;
  }
  return fields;
}

function createSourceConfig(type: string): WorkerSourceConfig {
  return {
    type,
    connector_id: null,
    fields: defaultFieldsForSchema(sourceTypeSchemas[type]),
  };
}

function createSinkConfig(type: string): WorkerSinkConfig {
  return {
    type,
    connector_id: null,
    fields: defaultFieldsForSchema(sinkTypeSchemas[type]),
  };
}

function isDeployableAgent(agent: WorkerAgentSummary) {
  return agent.status === "online" && agent.used_slots < agent.max_concurrent_deployments;
}

function getErrorMessage(error: unknown) {
  const message = error instanceof Error ? error.message : String(error);
  return message.replace(/\bworker\b/gi, (match) =>
    match[0] === "W" ? "Step" : "step",
  );
}

/**
 * Recursive file tree item with expand/collapse support for directories.
 */
function FileTreeItem({
  node,
  depth,
  activePath,
  expandedDirs,
  onToggleDir,
  onSelectFile,
}: {
  node: FileNode;
  depth: number;
  activePath: string;
  expandedDirs: Set<string>;
  onToggleDir: (path: string) => void;
  onSelectFile: (path: string) => void;
}) {
  const indent = { paddingLeft: `${10 + depth * 14}px` };

  if (node.type === "dir") {
    const isOpen = expandedDirs.has(node.path);
    return (
      <li className="worker-file-tree-dir">
        <button
          type="button"
          className="worker-file-tree-row worker-file-tree-folder"
          style={indent}
          onClick={() => onToggleDir(node.path)}
        >
          <span className="worker-file-tree-chevron" aria-hidden>
            {isOpen ? "▾" : "▸"}
          </span>
          <span className="worker-file-tree-name">{displayFileName(node)}</span>
        </button>
        {isOpen ? (
          <ul className="worker-file-tree-children">
            {node.children?.map((child) => (
              <FileTreeItem
                key={child.path}
                node={child}
                depth={depth + 1}
                activePath={activePath}
                expandedDirs={expandedDirs}
                onToggleDir={onToggleDir}
                onSelectFile={onSelectFile}
              />
            ))}
          </ul>
        ) : null}
      </li>
    );
  }

  const isActive = node.path === activePath;
  const isReadonly = node.path === "worker.yaml";
  return (
    <li>
      <button
        type="button"
        className={
          isActive
            ? "worker-file-tree-row worker-file-tree-file is-active"
            : "worker-file-tree-row worker-file-tree-file"
        }
        style={indent}
        onClick={() => onSelectFile(node.path)}
      >
        <span className="worker-file-tree-icon" aria-hidden>
          {isReadonly ? "◦" : "›"}
        </span>
        <span className="worker-file-tree-name">{displayFileName(node)}</span>
        {isReadonly ? <span className="worker-file-tree-tag">ro</span> : null}
      </button>
    </li>
  );
}

export function WorkerEditorPage() {
  const { workerId } = useParams<{ workerId: string }>();
  const isEditing = Boolean(workerId && workerId !== "new");
  const navigate = useNavigate();
  const { t } = useTranslation();
  const workerQuery = useWorkerQuery(workerId, isEditing);
  const connectorsQuery = useConnectorsQuery();
  const agentsQuery = useWorkerAgentsQuery();
  const createMutation = useCreateWorkerMutation();
  const updateMutation = useUpdateWorkerMutation(workerId ?? "");
  const deployMutation = useDeployWorkerMutation(workerId ?? "");
  const downloadPackageMutation = useDownloadWorkerPackageMutation(workerId ?? "");
  const packageMutation = useCreateWorkflowPackageMutation();

  const existing = isEditing ? workerQuery.data : undefined;
  const initialDraft = existing ? draftFromWorker(existing) : emptyDraft();

  const [draft, setDraft] = useState<Draft>(() => initialDraft);
  const [filesMap, setFilesMap] = useState<Record<string, string>>(() =>
    buildInitialFilesMap(initialDraft.handlerRef),
  );
  const [lastPackageId, setLastPackageId] = useState<string | null>(
    existing?.handler_package_id ?? null,
  );
  const [isPackageCurrent, setIsPackageCurrent] = useState(Boolean(existing?.handler_package_id));
  const [isTriggerDialogOpen, setIsTriggerDialogOpen] = useState(false);
  const [triggerDraft, setTriggerDraft] = useState<WorkerSourceConfig>(initialDraft.source);
  const [activeTab, setActiveTab] = useState<WorkerTab>("overview");
  const [activeConfigSection, setActiveConfigSection] = useState<string>("general");
  const [activeFilePath, setActiveFilePath] = useState<string>(() =>
    handlerModulePath(initialDraft.handlerRef),
  );
  const [expandedDirs, setExpandedDirs] = useState<Set<string>>(() => {
    const tree = buildTreeFromPaths(Object.keys(buildInitialFilesMap(initialDraft.handlerRef)));
    return new Set(collectDirectoryPaths(tree));
  });
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [deployingAgentId, setDeployingAgentId] = useState<string | null>(null);
  const [deployError, setDeployError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const agentConfigRef = useRef<HTMLDivElement>(null);

  const connectors = connectorsQuery.data?.items ?? [];
  const deployableAgents = (agentsQuery.data?.items ?? []).filter(isDeployableAgent);
  const hydratedWorkerIdRef = useRef<string | null>(existing?.id ?? null);

  const skipAutoSaveRef = useRef(true);
  useEffect(() => {
    if (!isEditing) {
      if (hydratedWorkerIdRef.current !== null) {
        const nextDraft = emptyDraft();
        const nextFilesMap = buildInitialFilesMap(nextDraft.handlerRef);
        const tree = buildTreeFromPaths(Object.keys(nextFilesMap));
        setDraft(nextDraft);
        setFilesMap(nextFilesMap);
        setLastPackageId(null);
        setIsPackageCurrent(false);
        setTriggerDraft(nextDraft.source);
        setActiveFilePath(handlerModulePath(nextDraft.handlerRef));
        setExpandedDirs(new Set(collectDirectoryPaths(tree)));
        hydratedWorkerIdRef.current = null;
        skipAutoSaveRef.current = true;
      }
      return;
    }
    if (!existing || hydratedWorkerIdRef.current === existing.id) return;

    const nextDraft = draftFromWorker(existing);
    const nextFilesMap = buildInitialFilesMap(nextDraft.handlerRef);
    const tree = buildTreeFromPaths(Object.keys(nextFilesMap));
    setDraft(nextDraft);
    setFilesMap(nextFilesMap);
    setLastPackageId(existing.handler_package_id ?? null);
    setIsPackageCurrent(Boolean(existing.handler_package_id));
    setTriggerDraft(nextDraft.source);
    setActiveFilePath(handlerModulePath(nextDraft.handlerRef));
    setExpandedDirs(new Set(collectDirectoryPaths(tree)));
    hydratedWorkerIdRef.current = existing.id;
    skipAutoSaveRef.current = true;
  }, [existing, isEditing]);

  // Auto-save: debounce draft changes to the backend (config only, no package).
  useEffect(() => {
    if (!isEditing || !workerId) return;
    if (skipAutoSaveRef.current) {
      skipAutoSaveRef.current = false;
      return;
    }
    const timer = setTimeout(() => {
      if (!canSaveReportingDraft(draft)) return;
      void updateMutation.mutateAsync(buildWorkerConfigPayload(draft, lastPackageId));
    }, 600);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    draft.name,
    draft.description,
    draft.handlerRef,
    draft.source,
    draft.sinks,
    draft.env,
    draft.reportingEnabled,
    draft.reportingConfig,
    draft.reportingToken,
  ]);

  function connectorTypeForResource(resourceType: string) {
    if (resourceType.startsWith("mysql_")) return "mysql";
    if (resourceType.startsWith("postgres_")) return "postgres";
    if (resourceType.startsWith("redis_")) return "redis";
    if (resourceType.startsWith("rabbitmq_")) return "rabbitmq";
    if (resourceType.startsWith("sqs_")) return "sqs";
    if (resourceType.startsWith("feishu_bitable_")) return "feishu_bitable";
    return null;
  }

  function connectorTypeForSource(sourceType: string) {
    return connectorTypeForResource(sourceType);
  }

  function connectorTypeForSink(sinkType: string) {
    return connectorTypeForResource(sinkType);
  }

  function openTriggerDialog() {
    setTriggerDraft({
      type: draft.source.type,
      connector_id: draft.source.connector_id,
      fields: { ...draft.source.fields },
    });
    setIsTriggerDialogOpen(true);
  }

  function setTriggerField(name: string, value: string) {
    setTriggerDraft({
      ...triggerDraft,
      fields: { ...triggerDraft.fields, [name]: value },
    });
  }

  function setTriggerFields(fields: Record<string, unknown>) {
    setTriggerDraft({
      ...triggerDraft,
      fields,
    });
  }

  function applyTrigger() {
    setDraft({ ...draft, source: normalizeSourceConfig(triggerDraft) });
    setIsTriggerDialogOpen(false);
  }

  function setSinkField(index: number, name: string, value: string) {
    const sinks = [...draft.sinks];
    sinks[index] = {
      ...sinks[index],
      fields: { ...sinks[index].fields, [name]: value },
    };
    setDraft({ ...draft, sinks });
  }

  function addSink() {
    setDraft({
      ...draft,
      sinks: [...draft.sinks, createSinkConfig("http_sink")],
    });
  }

  function removeSink(index: number) {
    setDraft({ ...draft, sinks: draft.sinks.filter((_, i) => i !== index) });
  }

  function addEnvVar() {
    setDraft({ ...draft, env: [...draft.env, createEnvVarDraft()] });
  }

  function setEnvVar(rowId: string, patch: Partial<Pick<EnvVarDraft, "key" | "value">>) {
    setDraft({
      ...draft,
      env: draft.env.map((row) => (row.id === rowId ? { ...row, ...patch } : row)),
    });
  }

  function removeEnvVar(rowId: string) {
    setDraft({ ...draft, env: draft.env.filter((row) => row.id !== rowId) });
  }

  async function handleSaveConfig() {
    const payload = buildWorkerConfigPayload(draft, lastPackageId);
    if (isEditing) {
      if (!workerId) return;
      await updateMutation.mutateAsync(payload);
      return;
    }

    const created = await createMutation.mutateAsync(payload);
    navigate(`/workers/${encodeURIComponent(created.id)}`);
  }

  function buildPackageArchive() {
    const entries = Object.entries(filesMap).map(([path, content]) => ({ path, content }));
    const blob = buildZipBlob(entries);
    const filename = `${safePackageName(draft.name || "new-step")}-handler.zip`;
    return {
      blob,
      entrypoint: handlerModulePath(draft.handlerRef),
      filename,
    };
  }

  async function handlePackageCode() {
    const archive = buildPackageArchive();
    const workflowPackage = await packageMutation.mutateAsync({
      file: archive.blob,
      workflowId: isEditing && workerId ? workerId : crypto.randomUUID(),
      version: new Date().toISOString(),
      filename: archive.filename,
      entrypoint: archive.entrypoint,
    });

    if (isEditing && workerId) {
      await updateMutation.mutateAsync(buildWorkerConfigPayload(draft, workflowPackage.package_id));
    }
    setLastPackageId(workflowPackage.package_id);
    setIsPackageCurrent(true);
  }

  async function handleDownloadPackage() {
    if (!isEditing || !workerId || !lastPackageId || !isPackageCurrent || !canSaveConfig) return;
    await updateMutation.mutateAsync(buildWorkerConfigPayload(draft, lastPackageId));
    const packageDownload = await downloadPackageMutation.mutateAsync();
    const url = URL.createObjectURL(packageDownload.blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = packageDownload.filename;
    document.body.append(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  function openAgentDeployConfig() {
    setActiveTab("deploy");
    setActiveConfigSection("agent");
    window.requestAnimationFrame(() => {
      if (typeof agentConfigRef.current?.scrollIntoView === "function") {
        agentConfigRef.current.scrollIntoView({ block: "start", behavior: "smooth" });
      }
    });
  }

  async function handleDeploy(agentId: string) {
    if (!workerId) return;
    setDeployError(null);
    setDeployingAgentId(agentId);
    try {
      const deployment = await deployMutation.mutateAsync({
        worker_agent_id: agentId,
        env: normalizeEnvRows(draft.env),
      });
      navigate(
        `/agents/${encodeURIComponent(agentId)}/deployments/${encodeURIComponent(
          deployment.deployment_id,
        )}/events`,
      );
    } catch (error) {
      setDeployError(getErrorMessage(error));
    } finally {
      setDeployingAgentId(null);
    }
  }

  const fileTree = useMemo(
    () => buildTreeFromPaths(Object.keys(filesMap)),
    [filesMap],
  );

  const activeFileNode = useMemo(() => {
    function flatten(nodes: FileNode[]): FileNode[] {
      return nodes.flatMap((node) =>
        node.type === "dir" && node.children ? flatten(node.children) : [node],
      );
    }
    return flatten(fileTree).find((node) => node.path === activeFilePath) ?? null;
  }, [fileTree, activeFilePath]);

  if (isEditing && workerQuery.isPending) {
    return <div className="loading-block">{t("workerEditor.loading")}</div>;
  }
  if (isEditing && !existing) {
    return <EmptyState title={t("workerEditor.notFoundTitle")} body={t("workerEditor.notFoundBody")} />;
  }

  const sourceSchema = sourceTypeSchemas[draft.source.type];
  const functionName = draft.name || t("workerEditor.newTitle");
  const sourceConnector = draft.source.connector_id
    ? connectors.find((connector) => connector.id === draft.source.connector_id)
    : null;
  const sourceDetail = sourceConnector
    ? `${sourceConnector.name} (${sourceConnector.type})`
    : sourceSchema?.needsConnector
      ? t("workerEditor.connectorUnset")
      : t("workerEditor.builtinSource");
  const triggerSchema = sourceTypeSchemas[triggerDraft.type];
  const triggerConnectorType = connectorTypeForSource(triggerDraft.type);
  const triggerConnectorOptions = triggerConnectorType
    ? connectors.filter((connector) => connector.type === triggerConnectorType)
    : connectors;
  const sourceParams = Object.entries(draft.source.fields)
    .filter(([, value]) => value !== null && value !== undefined && value !== "")
    .sort(([left], [right]) => sourceParamSortKey(left) - sourceParamSortKey(right));
  const canApplyTrigger =
    (!triggerSchema?.needsConnector || Boolean(triggerDraft.connector_id)) &&
    hasRequiredFields(triggerSchema, triggerDraft.fields);
  const activeConfigTitle =
    activeConfigSection === "handler"
      ? t("workerEditor.handlerRef")
      : activeConfigSection === "trigger"
        ? t("workerEditor.trigger")
        : activeConfigSection === "targets"
          ? t("workerEditor.targets")
          : activeConfigSection === "env"
            ? t("workerEditor.envVars")
            : activeConfigSection === "reporting"
              ? t("workerEditor.reporting")
              : activeConfigSection === "agent"
                ? t("workerEditor.agent")
                : t("workerEditor.generalConfig");
  const isSavingConfig = createMutation.isPending || updateMutation.isPending;
  const canSaveConfig = canSaveReportingDraft(draft);
  const sourceTypeLabel = (type: string) =>
    t(`workerEditor.sourceTypeLabels.${type}`, {
      defaultValue: sourceTypeSchemas[type]?.label ?? type,
    });
  const sinkTypeLabel = (type: string) =>
    t(`workerEditor.sinkTypeLabels.${type}`, {
      defaultValue: sinkTypeSchemas[type]?.label ?? type,
    });

  function sourceParamLabel(key: string) {
    return t(`workerEditor.sourceParamLabels.${draft.source.type}.${key}`, {
      defaultValue: key,
    });
  }

  function sourceParamValue(key: string, value: unknown) {
    if (draft.source.type === "interval" && (key === "minutes" || key === "seconds")) {
      return t("workerEditor.intervalParamFrequency", {
        amount: fieldDisplayValue(value),
        unit: t(`workerEditor.intervalUnitShort.${key}`),
      });
    }
    if (draft.source.type === "interval" && key === "immediate") {
      const normalized = fieldDisplayValue(value).trim().toLowerCase();
      if (normalized === "true") return t("workerEditor.intervalImmediateEnabled");
      if (normalized === "false") return t("workerEditor.intervalImmediateDisabled");
    }
    return fieldDisplayValue(value);
  }

  function renderTriggerFields() {
    if (triggerDraft.type === "interval") {
      const unit = intervalUnitForFields(triggerDraft.fields);
      const value = intervalValueForFields(triggerDraft.fields, unit);
      const immediate = immediateValueForFields(triggerDraft.fields);
      const unitLabel = t(`workerEditor.intervalUnitShort.${unit}`);

      return (
        <div className="worker-trigger-form">
          <section className="worker-trigger-guide">
            <div>
              <span>{t("workerEditor.intervalTriggerEyebrow")}</span>
              <strong>{t("workerEditor.intervalTriggerTitle")}</strong>
              <p>{t("workerEditor.intervalTriggerBody")}</p>
            </div>
            <div className="worker-trigger-guide-metric">
              <span>{t("workerEditor.intervalSchedulePreview")}</span>
              <strong>
                {value.trim()
                  ? t("workerEditor.intervalScheduleSummary", { amount: value, unit: unitLabel })
                  : t("workerEditor.intervalScheduleUnset")}
              </strong>
            </div>
          </section>

          <div className="worker-trigger-field-grid">
            <VibeField
              label={t("workerEditor.intervalRunEvery")}
              min="1"
              note={t("workerEditor.intervalRunEveryNote")}
              onChange={(event) =>
                setTriggerFields(
                  setIntervalScheduleFields(triggerDraft.fields, unit, event.target.value),
                )
              }
              type="number"
              value={value}
            />
            <VibehubSelect
              label={t("workerEditor.intervalUnit")}
              onChange={(nextValue) =>
                setTriggerFields(
                  setIntervalScheduleFields(
                    triggerDraft.fields,
                    nextValue as IntervalUnit,
                    intervalValueForFields(triggerDraft.fields, unit),
                  ),
                )
              }
              options={[
                { value: "minutes", label: t("workerEditor.intervalUnitMinutes") },
                { value: "seconds", label: t("workerEditor.intervalUnitSeconds") },
              ]}
              value={unit}
            />
          </div>

          <div className="worker-trigger-choice-group">
            <div className="worker-trigger-choice-head">
              <strong>{t("workerEditor.intervalStartupRun")}</strong>
              <p>{t("workerEditor.intervalStartupNote")}</p>
            </div>
            <div
              aria-label={t("workerEditor.intervalStartupRun")}
              className="worker-trigger-choice-grid"
              role="group"
            >
              {(["true", "false", ""] as ImmediateValue[]).map((option) => (
                <button
                  className={[
                    "worker-trigger-choice-option",
                    immediate === option ? "is-selected" : undefined,
                  ]
                    .filter(Boolean)
                    .join(" ")}
                  key={option || "unset"}
                  onClick={() =>
                    setTriggerFields(setIntervalImmediateField(triggerDraft.fields, option))
                  }
                  type="button"
                >
                  <strong>
                    {option === "true"
                      ? t("workerEditor.intervalStartupImmediate")
                      : option === "false"
                        ? t("workerEditor.intervalStartupDeferred")
                        : t("workerEditor.intervalStartupUnset")}
                  </strong>
                  <span>
                    {option === "true"
                      ? t("workerEditor.intervalStartupImmediateDesc")
                      : option === "false"
                        ? t("workerEditor.intervalStartupDeferredDesc")
                        : t("workerEditor.intervalStartupUnsetDesc")}
                  </span>
                </button>
              ))}
            </div>
          </div>
        </div>
      );
    }

    return triggerSchema?.fields.map((field) =>
      field.type === "select" && field.options ? (
        <VibehubSelect
          key={field.name}
          label={field.label}
          onChange={(nextValue) => setTriggerField(field.name, nextValue)}
          options={[
            { value: "", label: t("common.unset") },
            ...field.options.map((option) => ({ value: option, label: option })),
          ]}
          value={fieldDisplayValue(triggerDraft.fields[field.name])}
        />
      ) : (
        <VibeField
          key={field.name}
          label={field.label}
          onChange={(event) => setTriggerField(field.name, event.target.value)}
          placeholder={field.placeholder}
          type={fieldInputType(field)}
          value={fieldDisplayValue(triggerDraft.fields[field.name])}
        />
      ),
    );
  }

  function selectFile(path: string) {
    setActiveFilePath(path);
  }

  function toggleDir(path: string) {
    setExpandedDirs((prev) => {
      const next = new Set(prev);
      if (next.has(path)) {
        next.delete(path);
      } else {
        next.add(path);
      }
      return next;
    });
  }

  async function handleUploadZip(file: File) {
    setUploadError(null);
    try {
      const bytes = await readFileBytes(file);
      const parsed = parseZip(bytes);
      if (parsed.entries.length === 0) {
        setUploadError(t("workerEditor.uploadEmpty"));
        return;
      }
      const newMap: Record<string, string> = {};
      for (const entry of parsed.entries) {
        newMap[entry.path] = entry.content;
      }
      setFilesMap(newMap);
      setIsPackageCurrent(false);
      // Expand all directories by default.
      const tree = buildTreeFromPaths(Object.keys(newMap));
      setExpandedDirs(new Set(collectDirectoryPaths(tree)));
      // Select the first file.
      const firstFile = parsed.entries[0];
      if (firstFile) setActiveFilePath(firstFile.path);
    } catch {
      setUploadError(t("workerEditor.uploadFailed"));
    }
  }

  function handleFileInputChange(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (file) void handleUploadZip(file);
    // Reset so the same file can be re-uploaded.
    event.target.value = "";
  }

  const activeFileName = activeFileNode
    ? displayFileName(activeFileNode)
    : handlerFileName(draft.handlerRef);
  const isReadonlyFile = activeFilePath === "worker.yaml";
  const activeFileContent = filesMap[activeFilePath] ?? "";
  const packageStateLabel = lastPackageId && isPackageCurrent
    ? t("workerEditor.packageReady")
    : t("workerEditor.packageUnsaved");
  const reportingStateLabel = draft.reportingEnabled
    ? t("workerEditor.reportingActive")
    : t("workerEditor.reportingPaused");
  const sourceTypeName = sourceTypeLabel(draft.source.type);
  const deployableAgentCount = deployableAgents.length;

  return (
    <div
      className={[
        "ref-console-page signal-console-runtime-page worker-editor-page",
        isTriggerDialogOpen ? "has-open-modal" : undefined,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <SignalConsoleHeader
        kicker={t("workerEditor.eyebrow")}
        title={isEditing ? draft.name : t("workerEditor.newTitle")}
        description={
          <p className="signal-console-hero-note">
            {draft.description || t("workerEditor.subtitle")}
          </p>
        }
        side={
          <div className="signal-console-metric">
            <span>{t("workerEditor.sinkCount")}</span>
            <strong>{draft.sinks.length}</strong>
          </div>
        }
      />

      <div className="worker-tab-bar" role="tablist" aria-label={t("workerEditor.workerSetup")}>
        <SegmentedControl<WorkerTab>
          ariaLabel={t("workerEditor.workerSetup")}
          value={activeTab}
          onChange={setActiveTab}
          options={[
            { label: t("workerEditor.tabOverview"), value: "overview" },
            { label: t("workerEditor.tabConfig"), value: "config" },
            { label: t("workerEditor.tabCode"), value: "code" },
            { label: t("workerEditor.tabDeploy"), value: "deploy" },
          ]}
        />
      </div>

      <section
        className={
          activeTab === "overview"
            ? "worker-overview-panel worker-tab-panel is-active"
            : "worker-overview-panel worker-tab-panel"
        }
        role="tabpanel"
        hidden={activeTab !== "overview"}
      >
        <section className="worker-studio-surface">
          <header className="worker-studio-head">
            <div>
              <span>{t("workerEditor.overviewLabel")}</span>
              <h3>{t("workerEditor.overviewTitle")}</h3>
              <p>{t("workerEditor.overviewSubtitle")}</p>
            </div>
            <div className="worker-studio-status-strip">
              <span>{packageStateLabel}</span>
              <span>{reportingStateLabel}</span>
            </div>
          </header>

          <div className="worker-overview-grid">
            <article className="worker-overview-card">
              <span>{t("workerEditor.sourceSummary")}</span>
              <strong>{sourceTypeName}</strong>
              <p>{sourceDetail}</p>
            </article>
            <article className="worker-overview-card">
              <span>{t("workerEditor.handlerStage")}</span>
              <strong>{draft.handlerRef}</strong>
              <p>{packageStateLabel}</p>
            </article>
            <article className="worker-overview-card">
              <span>{t("workerEditor.sinksSummary")}</span>
              <strong>{t("workerEditor.targetCount", { count: draft.sinks.length })}</strong>
              <p>{draft.sinks.length > 0 ? t("workerEditor.targetsConfigured") : t("workerEditor.emptySinks")}</p>
            </article>
            <article className="worker-overview-card">
              <span>{t("workerEditor.agent")}</span>
              <strong>{t("workerEditor.deployableAgentCount", { count: deployableAgentCount })}</strong>
              <p>{t("workerEditor.agentHint")}</p>
            </article>
          </div>
        </section>
      </section>

      <section
        className={
          activeTab === "code"
            ? "worker-code-source worker-tab-panel is-active"
            : "worker-code-source worker-tab-panel"
        }
        role="tabpanel"
        hidden={activeTab !== "code"}
      >
        <header className="worker-code-source-head">
          <div className="worker-code-source-copy">
            <h3>{t("workerEditor.codeSource")}</h3>
            <p>{t("workerEditor.codeSourceSubtitle")}</p>
            {uploadError ? (
              <VibeInlineNotice as="span" variant="error">
                {uploadError}
              </VibeInlineNotice>
            ) : null}
          </div>
          <div className="worker-action-dock" aria-label={t("workerEditor.codeActions")} role="group">
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,application/zip"
              onChange={handleFileInputChange}
              style={{ display: "none" }}
            />
            <VibeButton
              className="worker-action-button worker-action-upload"
              onClick={() => fileInputRef.current?.click()}
              variant="secondary"
            >
              {t("workerEditor.uploadZip")}
            </VibeButton>
            <VibeButton
              className="worker-action-button worker-action-package"
              disabled={packageMutation.isPending}
              onClick={() => void handlePackageCode()}
              variant="primary"
            >
              {packageMutation.isPending
                ? t("workerEditor.packaging")
                : t("workerEditor.packageCode")}
            </VibeButton>
            <VibeButton
              className="worker-action-button worker-action-download"
              disabled={
                !isEditing ||
                !lastPackageId ||
                !isPackageCurrent ||
                !canSaveConfig ||
                updateMutation.isPending ||
                downloadPackageMutation.isPending
              }
              onClick={() => void handleDownloadPackage()}
              variant="secondary"
            >
              {t("workerEditor.downloadZip")}
            </VibeButton>
            {isEditing ? (
              <VibeButton
                className="worker-action-button worker-action-deploy"
                disabled={deployMutation.isPending}
                onClick={openAgentDeployConfig}
                variant="secondary"
              >
                {t("workerEditor.deploy")}
              </VibeButton>
            ) : null}
          </div>
        </header>

        <div className="worker-code-shell">
          <aside
            className="worker-code-explorer worker-file-tree"
            aria-label={t("workerEditor.fileTree")}
          >
            <div className="worker-file-tree-head">
              <strong>{functionName}</strong>
              <span>{t("workerEditor.fileTreeHint")}</span>
            </div>
            <ul className="worker-file-tree-root">
              {fileTree.map((node) => (
                <FileTreeItem
                  key={node.path}
                  node={node}
                  depth={0}
                  activePath={activeFilePath}
                  expandedDirs={expandedDirs}
                  onToggleDir={toggleDir}
                  onSelectFile={selectFile}
                />
              ))}
            </ul>
          </aside>
          <div className="worker-code-pane">
            <div className="worker-code-toolbar">
              <span>{activeFileName}</span>
              <span>
                {isReadonlyFile
                  ? t("workerEditor.fileReadonly")
                  : lastPackageId && isPackageCurrent
                    ? t("workerEditor.packageReady")
                    : t("workerEditor.packageUnsaved")}
              </span>
            </div>
            {isReadonlyFile ? (
              <div className="worker-code-placeholder" role="note">
                <p>{t("workerEditor.fileYamlPlaceholder")}</p>
              </div>
            ) : (
              <div className="worker-code-editor" aria-label={t("workerEditor.codeSource")}>
                <Editor
                  defaultLanguage={detectLanguage(activeFileName)}
                  path={activeFilePath}
                  height="360px"
                  options={{
                    fontFamily: "Space Mono, Menlo, Monaco, Consolas, monospace",
                    fontSize: 13,
                    lineHeight: 22,
                    minimap: { enabled: false },
                    padding: { top: 16, bottom: 16 },
                    scrollBeyondLastLine: false,
                    wordWrap: "on",
                    readOnly: false,
                  }}
                  value={activeFileContent}
                  onChange={(value) => {
                    setFilesMap((prev) => ({ ...prev, [activeFilePath]: value ?? "" }));
                    setIsPackageCurrent(false);
                  }}
                />
              </div>
            )}
          </div>
        </div>
      </section>

      <section
        className={
          activeTab === "config"
            ? "worker-config-panel worker-tab-panel is-active"
            : "worker-config-panel worker-tab-panel"
        }
        role="tabpanel"
        hidden={activeTab !== "config"}
      >
        <div className="worker-config-layout">
          <nav className="worker-config-sidebar" aria-label={t("workerEditor.tabConfig")}>
            <button
              type="button"
              className={activeConfigSection === "general" ? "is-active" : ""}
              onClick={() => setActiveConfigSection("general")}
            >
              {t("workerEditor.generalConfig")}
            </button>
            <button
              type="button"
              className={activeConfigSection === "handler" ? "is-active" : ""}
              onClick={() => setActiveConfigSection("handler")}
            >
              {t("workerEditor.handlerRef")}
            </button>
            <button
              type="button"
              className={activeConfigSection === "trigger" ? "is-active" : ""}
              onClick={() => setActiveConfigSection("trigger")}
            >
              {t("workerEditor.trigger")}
            </button>
            <button
              type="button"
              className={activeConfigSection === "targets" ? "is-active" : ""}
              onClick={() => setActiveConfigSection("targets")}
            >
              {t("workerEditor.targets")}
            </button>
            <button
              type="button"
              className={activeConfigSection === "env" ? "is-active" : ""}
              onClick={() => setActiveConfigSection("env")}
            >
              {t("workerEditor.envVars")}
            </button>
            <button
              type="button"
              className={activeConfigSection === "reporting" ? "is-active" : ""}
              onClick={() => setActiveConfigSection("reporting")}
            >
              {t("workerEditor.reporting")}
            </button>
            <button
              type="button"
              className={activeConfigSection === "agent" ? "is-active" : ""}
              onClick={() => setActiveConfigSection("agent")}
            >
              {t("workerEditor.agent")}
            </button>
          </nav>

          <div className="worker-config-content">
            <header className="worker-config-titlebar">
              <h3>{activeConfigTitle}</h3>
              <button
                type="button"
                className="worker-config-save"
                disabled={isSavingConfig || !canSaveConfig}
                onClick={() => void handleSaveConfig()}
              >
                {isSavingConfig ? t("workerEditor.savingConfig") : t("workerEditor.saveConfig")}
              </button>
            </header>

            {activeConfigSection === "general" ? (
              <WorkerConfigSection>
                <VibeField
                  label={t("workerEditor.name")}
                  onChange={(event) => setDraft({ ...draft, name: event.target.value })}
                  type="text"
                  value={draft.name}
                />
                <VibeField
                  label={t("workerEditor.description")}
                  multiline
                  onChange={(event) => setDraft({ ...draft, description: event.target.value })}
                  rows={3}
                  value={draft.description}
                />
              </WorkerConfigSection>
            ) : null}

            {activeConfigSection === "handler" ? (
              <WorkerConfigSection>
                <VibeField
                  label={t("workerEditor.handlerRef")}
                  onChange={(event) => {
                    setDraft({ ...draft, handlerRef: event.target.value });
                    setIsPackageCurrent(false);
                  }}
                  type="text"
                  value={draft.handlerRef}
                />
                <div className="worker-config-meta">
                  <div>
                    <span>{t("workerEditor.handlerPackage")}</span>
                    <strong>{lastPackageId ?? t("workerEditor.packageUnsaved")}</strong>
                  </div>
                </div>
              </WorkerConfigSection>
            ) : null}

            {activeConfigSection === "trigger" ? (
              <WorkerConfigSection
                actions={
                  <button type="button" onClick={openTriggerDialog}>
                    {t("workerEditor.configure")}
                  </button>
                }
              >
                <div className="worker-trigger-summary">
                  <div>
                    <span>{t("workerEditor.sourceType")}</span>
                    <strong>{sourceTypeLabel(draft.source.type)}</strong>
                  </div>
                  <div>
                    <span>{t("workerEditor.connector")}</span>
                    <strong>{sourceDetail}</strong>
                  </div>
                  <div className="worker-trigger-summary-full">
                    <span>{t("workerEditor.sourceParams")}</span>
                    {sourceParams.length > 0 ? (
                      <dl>
                        {sourceParams.map(([key, value]) => (
                          <div key={key}>
                            <dt>{sourceParamLabel(key)}</dt>
                            <dd>{sourceParamValue(key, value)}</dd>
                          </div>
                        ))}
                      </dl>
                    ) : (
                      <strong>{t("workerEditor.noSourceParams")}</strong>
                    )}
                  </div>
                </div>
              </WorkerConfigSection>
            ) : null}

            {activeConfigSection === "targets" ? (
              <WorkerConfigSection
                actions={
                  <button type="button" onClick={addSink}>
                    {t("workerEditor.addTarget")}
                  </button>
                }
              >
                <div className="runtime-sink-list">
                  {draft.sinks.length === 0 ? (
                    <VibeInlineNotice as="div" variant="empty">
                      {t("workerEditor.emptySinks")}
                    </VibeInlineNotice>
                  ) : null}
                  {draft.sinks.map((sink, index) => {
                    const sinkSchema = sinkTypeSchemas[sink.type];
                    const sinkConnectorType = connectorTypeForSink(sink.type);
                    const sinkConnectorOptions = sinkConnectorType
                      ? connectors.filter((connector) => connector.type === sinkConnectorType)
                      : connectors;
                    return (
                      <div className="ref-accordion-item runtime-sink-card" key={index}>
                        <VibehubSelect
                          label={`${t("workerEditor.sinkType")} ${index + 1}`}
                          onChange={(nextValue) => {
                            const sinks = [...draft.sinks];
                            sinks[index] = createSinkConfig(nextValue);
                            setDraft({ ...draft, sinks });
                          }}
                          options={sinkTypeOrder.map((typ) => ({
                            value: typ,
                            label: sinkTypeLabel(typ),
                          }))}
                          value={sink.type}
                        />
                        {sinkSchema?.needsConnector ? (
                          <VibehubSelect
                            label={t("workerEditor.connector")}
                            onChange={(nextValue) => {
                              const sinks = [...draft.sinks];
                              sinks[index] = { ...sinks[index], connector_id: nextValue };
                              setDraft({ ...draft, sinks });
                            }}
                            options={[
                              { value: "", label: t("workerEditor.selectConnector") },
                              ...sinkConnectorOptions.map((connector) => ({
                                value: connector.id,
                                label: `${connector.name} (${connector.type})`,
                              })),
                            ]}
                            value={sink.connector_id ?? ""}
                          />
                        ) : null}
                        {sinkSchema?.needsConnector && sinkConnectorOptions.length === 0 ? (
                          <VibeInlineNotice variant="error">
                            {t("workerEditor.noAvailableConnectors")}
                          </VibeInlineNotice>
                        ) : null}
                        {sinkSchema?.fields.filter((field) => shouldRenderSinkField(sink, field)).map((field) => (
                          field.type === "select" ? (
                            <VibehubSelect
                              key={field.name}
                              label={field.label}
                              onChange={(nextValue) => setSinkField(index, field.name, nextValue)}
                              options={(field.options ?? []).map((option) => ({
                                value: option,
                                label: option,
                              }))}
                              value={fieldDisplayValue(sink.fields[field.name])}
                            />
                          ) : field.type === "json" ? (
                            <VibeField
                              key={field.name}
                              label={field.label}
                              multiline
                              onChange={(event) => setSinkField(index, field.name, event.target.value)}
                              placeholder={field.placeholder}
                              rows={6}
                              value={fieldDisplayValue(sink.fields[field.name])}
                            />
                          ) : (
                            <VibeField
                              key={field.name}
                              label={field.label}
                              onChange={(event) => setSinkField(index, field.name, event.target.value)}
                              placeholder={field.placeholder}
                              type={fieldInputType(field)}
                              value={fieldDisplayValue(sink.fields[field.name])}
                            />
                          )
                        ))}
                        <button type="button" onClick={() => removeSink(index)}>
                          {t("workerEditor.removeSink")}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </WorkerConfigSection>
            ) : null}

            {activeConfigSection === "env" ? (
              <WorkerConfigSection
                actions={
                  <button type="button" onClick={addEnvVar}>
                    {t("workerEditor.addEnvVar")}
                  </button>
                }
              >
                <div className="worker-env-list">
                  {draft.env.length === 0 ? (
                    <VibeInlineNotice variant="empty">
                      {t("workerEditor.envVarsEmpty")}
                    </VibeInlineNotice>
                  ) : null}
                  {draft.env.map((row, index) => (
                    <WorkerEnvRow
                      envKey={row.key}
                      envValue={row.value}
                      key={row.id}
                      keyAriaLabel={`${t("workerEditor.envKey")} ${index + 1}`}
                      keyLabel={t("workerEditor.envKey")}
                      keyPlaceholder={t("workerEditor.envKeyPlaceholder")}
                      onKeyChange={(value) => setEnvVar(row.id, { key: value })}
                      onRemove={() => removeEnvVar(row.id)}
                      onValueChange={(value) => setEnvVar(row.id, { value })}
                      removeLabel={t("workerEditor.removeEnvVar")}
                      valueAriaLabel={`${t("workerEditor.envValue")} ${index + 1}`}
                      valueLabel={t("workerEditor.envValue")}
                      valuePlaceholder={t("workerEditor.envValuePlaceholder")}
                    />
                  ))}
                </div>
              </WorkerConfigSection>
            ) : null}

            {activeConfigSection === "reporting" ? (
              <WorkerConfigSection>
                <div className="worker-reporting-toggle-row">
                  <div className="worker-reporting-toggle-copy">
                    <strong>{t("workerEditor.reportingEnabled")}</strong>
                    <span>
                      {draft.reportingEnabled
                        ? t("workerEditor.reportingEnabledOn")
                        : t("workerEditor.reportingEnabledOff")}
                    </span>
                  </div>
                  <label className="worker-reporting-switch">
                    <input
                      aria-label={t("workerEditor.reportingEnabled")}
                      role="switch"
                      type="checkbox"
                      checked={draft.reportingEnabled}
                      onChange={(event) =>
                        setDraft({ ...draft, reportingEnabled: event.target.checked })
                      }
                    />
                    <span className="worker-reporting-switch-track" aria-hidden="true">
                      <span className="worker-reporting-switch-thumb" />
                    </span>
                  </label>
                </div>
                <VibehubSelect
                  label={t("workerEditor.reportingMode")}
                  onChange={(nextValue) =>
                    setDraft({
                      ...draft,
                      reportingConfig:
                        nextValue === "custom"
                          ? {
                              mode: "custom",
                              endpoint_url: draft.reportingConfig.endpoint_url ?? "",
                            }
                          : { mode: "platform", endpoint_url: null },
                      reportingToken: nextValue === "custom" ? draft.reportingToken : "",
                      reportingTokenConfigured:
                        nextValue === "custom" ? draft.reportingTokenConfigured : false,
                    })
                  }
                  options={[
                    { value: "platform", label: t("workerEditor.reportingModePlatform") },
                    { value: "custom", label: t("workerEditor.reportingModeCustom") },
                  ]}
                  value={draft.reportingConfig.mode}
                />
                {draft.reportingConfig.mode === "custom" ? (
                  <>
                    <VibeField
                      label={t("workerEditor.reportingEndpoint")}
                      onChange={(event) =>
                        setDraft({
                          ...draft,
                          reportingConfig: {
                            mode: "custom",
                            endpoint_url: event.target.value,
                          },
                        })
                      }
                      type="url"
                      value={draft.reportingConfig.endpoint_url ?? ""}
                    />
                    <VibeField
                      aria-label={t("workerEditor.reportingToken")}
                      label={t("workerEditor.reportingToken")}
                      onChange={(event) =>
                        setDraft({ ...draft, reportingToken: event.target.value })
                      }
                      placeholder={
                        draft.reportingTokenConfigured
                          ? t("workerEditor.reportingTokenPlaceholderConfigured")
                          : t("workerEditor.reportingTokenPlaceholder")
                      }
                      type="password"
                      value={draft.reportingToken}
                    />
                    {draft.reportingTokenConfigured ? (
                      <VibeInlineNotice variant="empty">
                        {t("workerEditor.reportingTokenConfigured")}
                      </VibeInlineNotice>
                    ) : null}
                    {!canSaveConfig ? (
                      <VibeInlineNotice variant="error">
                        {t("workerEditor.reportingCustomRequired")}
                      </VibeInlineNotice>
                    ) : null}
                  </>
                ) : (
                  <VibeInlineNotice variant="empty">
                    {t("workerEditor.reportingPlatformHint")}
                  </VibeInlineNotice>
                )}
              </WorkerConfigSection>
            ) : null}

            {activeTab === "config" && activeConfigSection === "agent" ? (
              <WorkerConfigSection ref={agentConfigRef}>
                <VibeInlineNotice variant="empty">
                  {t("workerEditor.agentHint")}
                </VibeInlineNotice>
                {agentsQuery.error ? (
                  <VibeInlineNotice variant="error">
                    {t("workerEditor.agentLoadError")}
                  </VibeInlineNotice>
                ) : null}
                {deployError ? (
                  <VibeInlineNotice variant="error">
                    {t("workerEditor.deployFailed")}: {deployError}
                  </VibeInlineNotice>
                ) : null}
                {agentsQuery.isPending ? (
                  <div className="loading-block">{t("workerEditor.agentLoading")}</div>
                ) : null}
                {!agentsQuery.isPending && !agentsQuery.error && deployableAgents.length === 0 ? (
                  <VibeInlineNotice variant="empty">
                    {t("workerEditor.noDeployableAgents")}
                  </VibeInlineNotice>
                ) : null}
                {deployableAgents.length > 0 ? (
                  <div className="worker-agent-deploy-list">
                    {deployableAgents.map((agent) => (
                      <WorkerAgentDeployCard
                        agent={agent}
                        key={agent.worker_agent_id}
                        disabled={!isEditing || deployMutation.isPending || Boolean(deployingAgentId)}
                        deploying={deployingAgentId === agent.worker_agent_id}
                        deployingLabel={t("workerEditor.deploying")}
                        fallbackVersionLabel={t("common.notAvailable")}
                        onDeploy={(agentId) => void handleDeploy(agentId)}
                      />
                    ))}
                  </div>
                ) : null}
              </WorkerConfigSection>
            ) : null}
          </div>
        </div>
      </section>

      <section
        className={
          activeTab === "deploy"
            ? "worker-deploy-panel worker-tab-panel is-active"
            : "worker-deploy-panel worker-tab-panel"
        }
        role="tabpanel"
        hidden={activeTab !== "deploy"}
      >
        <section className="worker-studio-surface">
          <header className="worker-studio-head">
            <div>
              <span>{t("workerEditor.deployEyebrow")}</span>
              <h3>{t("workerEditor.deployTitle")}</h3>
              <p>{t("workerEditor.deploySubtitle")}</p>
            </div>
          </header>
          <WorkerConfigSection ref={agentConfigRef}>
            <VibeInlineNotice variant="empty">
              {t("workerEditor.agentHint")}
            </VibeInlineNotice>
            {agentsQuery.error ? (
              <VibeInlineNotice variant="error">
                {t("workerEditor.agentLoadError")}
              </VibeInlineNotice>
            ) : null}
            {deployError ? (
              <VibeInlineNotice variant="error">
                {t("workerEditor.deployFailed")}: {deployError}
              </VibeInlineNotice>
            ) : null}
            {agentsQuery.isPending ? (
              <div className="loading-block">{t("workerEditor.agentLoading")}</div>
            ) : null}
            {!agentsQuery.isPending && !agentsQuery.error && deployableAgents.length === 0 ? (
              <VibeInlineNotice variant="empty">
                {t("workerEditor.noDeployableAgents")}
              </VibeInlineNotice>
            ) : null}
            {deployableAgents.length > 0 ? (
              <div className="worker-agent-deploy-list">
                {deployableAgents.map((agent) => (
                  <WorkerAgentDeployCard
                    agent={agent}
                    key={agent.worker_agent_id}
                    disabled={!isEditing || deployMutation.isPending || Boolean(deployingAgentId)}
                    deploying={deployingAgentId === agent.worker_agent_id}
                    deployingLabel={t("workerEditor.deploying")}
                    fallbackVersionLabel={t("common.notAvailable")}
                    onDeploy={(agentId) => void handleDeploy(agentId)}
                  />
                ))}
              </div>
            ) : null}
          </WorkerConfigSection>
        </section>
      </section>

      <VibeModal
        className="worker-trigger-modal"
        footer={
          <>
            <VibeButton onClick={() => setIsTriggerDialogOpen(false)} variant="secondary">
              {t("common.cancel")}
            </VibeButton>
            <VibeButton disabled={!canApplyTrigger} onClick={applyTrigger} variant="primary">
              {t("workerEditor.applyTrigger")}
            </VibeButton>
          </>
        }
        onClose={() => setIsTriggerDialogOpen(false)}
        open={isTriggerDialogOpen}
        title={t("workerEditor.configureTrigger")}
      >
        <VibehubSelect
          label={t("workerEditor.sourceType")}
          onChange={(nextValue) => setTriggerDraft(createSourceConfig(nextValue))}
          options={sourceTypeOrder.map((typ) => ({
            value: typ,
            label: sourceTypeLabel(typ),
          }))}
          value={triggerDraft.type}
        />
        {triggerSchema?.needsConnector ? (
          <VibehubSelect
            label={t("workerEditor.availableConnector")}
            onChange={(nextValue) =>
              setTriggerDraft({ ...triggerDraft, connector_id: nextValue })
            }
            options={[
              { value: "", label: t("workerEditor.selectConnector") },
              ...triggerConnectorOptions.map((connector) => ({
                value: connector.id,
                label: `${connector.name} (${connector.type})`,
              })),
            ]}
            value={triggerDraft.connector_id ?? ""}
          />
        ) : null}
        {triggerSchema?.needsConnector && triggerConnectorOptions.length === 0 ? (
          <VibeInlineNotice variant="error">
            {t("workerEditor.noAvailableConnectors")}
          </VibeInlineNotice>
        ) : null}
        {renderTriggerFields()}
      </VibeModal>
    </div>
  );
}

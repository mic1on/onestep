import { useEffect, useMemo, useRef, useState } from "react";
import Editor from "@monaco-editor/react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { StatusBadge } from "../../components/ui/StatusBadge";
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

type WorkerTab = "code" | "config";

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
    source: { type: "interval", connector_id: null, fields: {} },
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
  const workerName = draft.name || "new-worker";
  return [
    "from __future__ import annotations",
    "",
    "from typing import Any",
    "",
    "",
    `async def ${functionName}(ctx, item) -> dict[str, Any]:`,
    `    """Handler for ${workerName}."""`,
    "    payload = item",
    "    # Add business logic here; source and sink wiring stays in worker.yaml.",
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
  return safe || "worker";
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
  if (value === null || value === undefined) return "";
  return String(value);
}

function fieldInputType(field: SourceSinkField) {
  if (field.type === "password") return "password";
  if (field.type === "number") return "number";
  return "text";
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
  return value;
}

function normalizeConfigFields(schema: SourceSinkTypeSchema | undefined, fields: Record<string, unknown>) {
  if (!schema) return fields;
  const normalized: Record<string, unknown> = {};
  for (const field of schema.fields) {
    const value = normalizeFieldValue(field, fields[field.name]);
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
  return {
    ...sink,
    fields: normalizeConfigFields(sinkTypeSchemas[sink.type], sink.fields),
  };
}

function isDeployableAgent(agent: WorkerAgentSummary) {
  return agent.status === "online" && agent.used_slots < agent.max_concurrent_deployments;
}

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
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
          <span className="worker-file-tree-name">{node.name}</span>
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
        <span className="worker-file-tree-name">{node.name}</span>
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
  const [isTriggerDialogOpen, setIsTriggerDialogOpen] = useState(false);
  const [triggerDraft, setTriggerDraft] = useState<WorkerSourceConfig>(initialDraft.source);
  // Default to the config tab so the labelled "Name" input is reachable by tests
  // and the form is the first thing a user interacts with.
  const [activeTab, setActiveTab] = useState<WorkerTab>("config");
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

  function connectorTypeForSource(sourceType: string) {
    if (sourceType.startsWith("mysql_")) return "mysql";
    if (sourceType.startsWith("postgres_")) return "postgres";
    if (sourceType.startsWith("redis_")) return "redis";
    if (sourceType.startsWith("rabbitmq_")) return "rabbitmq";
    if (sourceType.startsWith("sqs_")) return "sqs";
    return null;
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
      sinks: [...draft.sinks, { type: "http_sink", connector_id: null, fields: {} }],
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

  async function handlePackageCode() {
    // Build zip from the full filesMap (all uploaded/edited files).
    const entries = Object.entries(filesMap).map(([path, content]) => ({ path, content }));
    const blob = buildZipBlob(entries);
    const handlerPath = handlerModulePath(draft.handlerRef);
    const workflowPackage = await packageMutation.mutateAsync({
      file: blob,
      workflowId: isEditing && workerId ? workerId : crypto.randomUUID(),
      version: new Date().toISOString(),
      filename: `${safePackageName(draft.name || "new-worker")}-handler.zip`,
      entrypoint: handlerPath,
    });
    setLastPackageId(workflowPackage.package_id);

    if (isEditing && workerId) {
      // Only update the package binding; config fields auto-save separately.
      await updateMutation.mutateAsync({
        handler_package_id: workflowPackage.package_id,
      });
    }
  }

  function openAgentDeployConfig() {
    setActiveTab("config");
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
  const sinkSummary = draft.sinks.length
    ? t("workerEditor.targetCount", { count: draft.sinks.length })
    : t("workerEditor.noTargets");
  const triggerSchema = sourceTypeSchemas[triggerDraft.type];
  const triggerConnectorType = connectorTypeForSource(triggerDraft.type);
  const triggerConnectorOptions = triggerConnectorType
    ? connectors.filter((connector) => connector.type === triggerConnectorType)
    : connectors;
  const sourceParams = Object.entries(draft.source.fields).filter(
    ([, value]) => value !== null && value !== undefined && value !== "",
  );
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

  const activeFileName = activeFileNode?.name ?? handlerFileName(draft.handlerRef);
  const isReadonlyFile = activeFilePath === "worker.yaml";
  const activeFileContent = filesMap[activeFilePath] ?? "";

  return (
    <div className="ref-console-page signal-console-runtime-page worker-editor-page">
      <SignalConsoleHeader
        kicker={t("workerEditor.eyebrow")}
        title={isEditing ? draft.name : t("workerEditor.newTitle")}
        description={<p className="signal-console-hero-note">{t("workerEditor.subtitle")}</p>}
        side={
          <div className="signal-console-metric">
            <span>{t("workerEditor.sinkCount")}</span>
            <strong>{draft.sinks.length}</strong>
          </div>
        }
      />

      <section className="worker-build-panel">
        <header className="worker-build-head">
          <div>
            <h3>{t("workerEditor.workerSetup")}</h3>
            <p>{t("workerEditor.workerSetupSubtitle")}</p>
          </div>
          <dl>
            <div>
              <dt>{t("workerEditor.sourceStage")}</dt>
              <dd>{sourceSchema?.label ?? draft.source.type}</dd>
            </div>
            <div>
              <dt>{t("workerEditor.sinkStage")}</dt>
              <dd>{sinkSummary}</dd>
            </div>
            <div>
              <dt>{t("workerEditor.handlerPackage")}</dt>
              <dd>{lastPackageId ?? t("workerEditor.packageUnsaved")}</dd>
            </div>
          </dl>
        </header>

        <div className="worker-build-grid">
          <section className="runtime-form-section runtime-form-section-wide">
            <div className="runtime-section-heading">
              <span>01</span>
              <h3>{t("workerEditor.identity")}</h3>
            </div>
            <div className="worker-identity-summary">
              <div>
                <span>{t("workerEditor.name")}</span>
                <strong>{draft.name || t("workerEditor.newTitle")}</strong>
              </div>
              <div>
                <span>{t("workerEditor.description")}</span>
                <strong>{draft.description || "—"}</strong>
              </div>
            </div>
          </section>

          <section className="runtime-form-section">
            <div className="runtime-section-heading runtime-section-heading-actions">
              <div>
                <span>02</span>
                <h3>{t("workerEditor.source")}</h3>
              </div>
              <button type="button" onClick={openTriggerDialog}>
                {t("workerEditor.addTrigger")}
              </button>
            </div>
            <div className="worker-overview-summary">
              <span>{t("workerEditor.sourceType")}</span>
              <strong>{sourceSchema?.label ?? draft.source.type}</strong>
            </div>
          </section>

          <section className="runtime-form-section">
            <div className="runtime-section-heading runtime-section-heading-actions">
              <div>
                <span>03</span>
                <h3>{t("workerEditor.sinks")}</h3>
              </div>
              <button type="button" onClick={addSink}>
                {t("workerEditor.addSink")}
              </button>
            </div>
            <div className="worker-overview-summary">
              <span>{t("workerEditor.sinkStage")}</span>
              <strong>{sinkSummary}</strong>
            </div>
          </section>
        </div>

        <div className="ref-page-actions runtime-editor-actions">
          <button
            type="button"
            disabled={packageMutation.isPending}
            onClick={() => void handlePackageCode()}
          >
            {packageMutation.isPending ? t("workerEditor.packaging") : t("workerEditor.packageCode")}
          </button>
          {isEditing ? (
            <button
              type="button"
              disabled={deployMutation.isPending}
              onClick={openAgentDeployConfig}
            >
              {t("workerEditor.deploy")}
            </button>
          ) : null}
        </div>
      </section>

      <div className="worker-tab-bar" role="tablist" aria-label={t("workerEditor.workerSetup")}>
        <SegmentedControl<WorkerTab>
          ariaLabel={t("workerEditor.workerSetup")}
          value={activeTab}
          onChange={setActiveTab}
          options={[
            { label: t("workerEditor.tabCode"), value: "code" },
            { label: t("workerEditor.tabConfig"), value: "config" },
          ]}
        />
      </div>

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
          <div>
            <h3>{t("workerEditor.codeSource")}</h3>
            <p>{t("workerEditor.codeSourceSubtitle")}</p>
          </div>
          <div className="worker-code-source-actions">
            <input
              ref={fileInputRef}
              type="file"
              accept=".zip,application/zip"
              onChange={handleFileInputChange}
              style={{ display: "none" }}
            />
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
            >
              {t("workerEditor.uploadZip")}
            </button>
            {uploadError ? (
              <span className="ref-error-note">{uploadError}</span>
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
                  : lastPackageId
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
                  onChange={(value) =>
                    setFilesMap((prev) => ({ ...prev, [activeFilePath]: value ?? "" }))
                  }
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
              <div className="worker-config-section">
                <label className="ref-inline-control">
                  <span>{t("workerEditor.name")}</span>
                  <input
                    type="text"
                    value={draft.name}
                    onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                  />
                </label>
                <label className="ref-inline-control">
                  <span>{t("workerEditor.description")}</span>
                  <textarea
                    rows={3}
                    value={draft.description}
                    onChange={(e) => setDraft({ ...draft, description: e.target.value })}
                  />
                </label>
              </div>
            ) : null}

            {activeConfigSection === "handler" ? (
              <div className="worker-config-section">
                <label className="ref-inline-control">
                  <span>{t("workerEditor.handlerRef")}</span>
                  <input
                    type="text"
                    value={draft.handlerRef}
                    onChange={(e) => setDraft({ ...draft, handlerRef: e.target.value })}
                  />
                </label>
                <div className="worker-config-meta">
                  <div>
                    <span>{t("workerEditor.handlerPackage")}</span>
                    <strong>{lastPackageId ?? t("workerEditor.packageUnsaved")}</strong>
                  </div>
                </div>
              </div>
            ) : null}

            {activeConfigSection === "trigger" ? (
              <div className="worker-config-section">
                <div className="worker-config-section-actions">
                  <button type="button" onClick={openTriggerDialog}>
                    {t("workerEditor.configure")}
                  </button>
                </div>
                <div className="worker-trigger-summary">
                  <div>
                    <span>{t("workerEditor.sourceType")}</span>
                    <strong>{sourceSchema?.label ?? draft.source.type}</strong>
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
                            <dt>{key}</dt>
                            <dd>{fieldDisplayValue(value)}</dd>
                          </div>
                        ))}
                      </dl>
                    ) : (
                      <strong>{t("workerEditor.noSourceParams")}</strong>
                    )}
                  </div>
                </div>
              </div>
            ) : null}

            {activeConfigSection === "targets" ? (
              <div className="worker-config-section">
                <div className="worker-config-section-actions">
                  <button type="button" onClick={addSink}>
                    {t("workerEditor.addTarget")}
                  </button>
                </div>
                <div className="runtime-sink-list">
                  {draft.sinks.length === 0 ? (
                    <div className="runtime-empty-inline">{t("workerEditor.emptySinks")}</div>
                  ) : null}
                  {draft.sinks.map((sink, index) => {
                    const sinkSchema = sinkTypeSchemas[sink.type];
                    return (
                      <div className="ref-accordion-item runtime-sink-card" key={index}>
                        <label className="ref-inline-control ref-inline-control-select">
                          <span>
                            {t("workerEditor.sinkType")} {index + 1}
                          </span>
                          <select
                            value={sink.type}
                            onChange={(e) => {
                              const sinks = [...draft.sinks];
                              sinks[index] = { type: e.target.value, connector_id: null, fields: {} };
                              setDraft({ ...draft, sinks });
                            }}
                          >
                            {sinkTypeOrder.map((typ) => (
                              <option key={typ} value={typ}>
                                {sinkTypeSchemas[typ].label}
                              </option>
                            ))}
                          </select>
                        </label>
                        {sinkSchema?.needsConnector ? (
                          <label className="ref-inline-control ref-inline-control-select">
                            <span>{t("workerEditor.connector")}</span>
                            <select
                              value={sink.connector_id ?? ""}
                              onChange={(e) => {
                                const sinks = [...draft.sinks];
                                sinks[index] = { ...sinks[index], connector_id: e.target.value };
                                setDraft({ ...draft, sinks });
                              }}
                            >
                              <option value="">{t("workerEditor.selectConnector")}</option>
                              {connectors.map((c) => (
                                <option key={c.id} value={c.id}>
                                  {c.name} ({c.type})
                                </option>
                              ))}
                            </select>
                          </label>
                        ) : null}
                        {sinkSchema?.fields.map((field) => (
                          <label className="ref-inline-control" key={field.name}>
                            <span>{field.label}</span>
                            <input
                              type={fieldInputType(field)}
                              placeholder={field.placeholder}
                              value={fieldDisplayValue(sink.fields[field.name])}
                              onChange={(e) => setSinkField(index, field.name, e.target.value)}
                            />
                          </label>
                        ))}
                        <button type="button" onClick={() => removeSink(index)}>
                          {t("workerEditor.removeSink")}
                        </button>
                      </div>
                    );
                  })}
                </div>
              </div>
            ) : null}

            {activeConfigSection === "env" ? (
              <div className="worker-config-section">
                <div className="worker-config-section-actions">
                  <button type="button" onClick={addEnvVar}>
                    {t("workerEditor.addEnvVar")}
                  </button>
                </div>
                <div className="worker-env-list">
                  {draft.env.length === 0 ? (
                    <p className="runtime-empty-inline">{t("workerEditor.envVarsEmpty")}</p>
                  ) : null}
                  {draft.env.map((row, index) => (
                    <div className="worker-env-row" key={row.id}>
                      <label className="ref-inline-control">
                        <span>{t("workerEditor.envKey")}</span>
                        <input
                          type="text"
                          aria-label={`${t("workerEditor.envKey")} ${index + 1}`}
                          placeholder={t("workerEditor.envKeyPlaceholder")}
                          value={row.key}
                          onChange={(e) => setEnvVar(row.id, { key: e.target.value })}
                        />
                      </label>
                      <label className="ref-inline-control">
                        <span>{t("workerEditor.envValue")}</span>
                        <input
                          type="text"
                          aria-label={`${t("workerEditor.envValue")} ${index + 1}`}
                          placeholder={t("workerEditor.envValuePlaceholder")}
                          value={row.value}
                          onChange={(e) => setEnvVar(row.id, { value: e.target.value })}
                        />
                      </label>
                      <button
                        type="button"
                        className="worker-env-remove"
                        onClick={() => removeEnvVar(row.id)}
                      >
                        {t("workerEditor.removeEnvVar")}
                      </button>
                    </div>
                  ))}
                </div>
              </div>
            ) : null}

            {activeConfigSection === "reporting" ? (
              <div className="worker-config-section">
                <label className="ref-inline-control">
                  <span>{t("workerEditor.reportingEnabled")}</span>
                  <input
                    aria-label={t("workerEditor.reportingEnabled")}
                    type="checkbox"
                    checked={draft.reportingEnabled}
                    onChange={(event) =>
                      setDraft({ ...draft, reportingEnabled: event.target.checked })
                    }
                  />
                </label>
                <label className="ref-inline-control ref-inline-control-select">
                  <span>{t("workerEditor.reportingMode")}</span>
                  <select
                    value={draft.reportingConfig.mode}
                    onChange={(event) =>
                      setDraft({
                        ...draft,
                        reportingConfig:
                          event.target.value === "custom"
                            ? {
                                mode: "custom",
                                endpoint_url: draft.reportingConfig.endpoint_url ?? "",
                              }
                            : { mode: "platform", endpoint_url: null },
                        reportingToken: event.target.value === "custom" ? draft.reportingToken : "",
                        reportingTokenConfigured:
                          event.target.value === "custom"
                            ? draft.reportingTokenConfigured
                            : false,
                      })
                    }
                  >
                    <option value="platform">{t("workerEditor.reportingModePlatform")}</option>
                    <option value="custom">{t("workerEditor.reportingModeCustom")}</option>
                  </select>
                </label>
                {draft.reportingConfig.mode === "custom" ? (
                  <>
                    <label className="ref-inline-control">
                      <span>{t("workerEditor.reportingEndpoint")}</span>
                      <input
                        type="url"
                        value={draft.reportingConfig.endpoint_url ?? ""}
                        onChange={(event) =>
                          setDraft({
                            ...draft,
                            reportingConfig: {
                              mode: "custom",
                              endpoint_url: event.target.value,
                            },
                          })
                        }
                      />
                    </label>
                    <label className="ref-inline-control">
                      <span>{t("workerEditor.reportingToken")}</span>
                      <input
                        aria-label={t("workerEditor.reportingToken")}
                        type="password"
                        placeholder={
                          draft.reportingTokenConfigured
                            ? t("workerEditor.reportingTokenPlaceholderConfigured")
                            : t("workerEditor.reportingTokenPlaceholder")
                        }
                        value={draft.reportingToken}
                        onChange={(event) =>
                          setDraft({ ...draft, reportingToken: event.target.value })
                        }
                      />
                    </label>
                    {draft.reportingTokenConfigured ? (
                      <p className="runtime-empty-inline">
                        {t("workerEditor.reportingTokenConfigured")}
                      </p>
                    ) : null}
                    {!canSaveConfig ? (
                      <p className="ref-error-note">{t("workerEditor.reportingCustomRequired")}</p>
                    ) : null}
                  </>
                ) : (
                  <p className="runtime-empty-inline">{t("workerEditor.reportingPlatformHint")}</p>
                )}
              </div>
            ) : null}

            {activeConfigSection === "agent" ? (
              <div className="worker-config-section" ref={agentConfigRef}>
                <p className="runtime-empty-inline">{t("workerEditor.agentHint")}</p>
                {agentsQuery.error ? (
                  <p className="ref-error-note">{t("workerEditor.agentLoadError")}</p>
                ) : null}
                {deployError ? (
                  <p className="ref-error-note">
                    {t("workerEditor.deployFailed")}: {deployError}
                  </p>
                ) : null}
                {agentsQuery.isPending ? (
                  <div className="loading-block">{t("workerEditor.agentLoading")}</div>
                ) : null}
                {!agentsQuery.isPending && !agentsQuery.error && deployableAgents.length === 0 ? (
                  <p className="runtime-empty-inline">{t("workerEditor.noDeployableAgents")}</p>
                ) : null}
                {deployableAgents.length > 0 ? (
                  <div className="worker-agent-deploy-list">
                    {deployableAgents.map((agent) => (
                      <button
                        type="button"
                        className="worker-agent-deploy-card"
                        key={agent.worker_agent_id}
                        disabled={!isEditing || deployMutation.isPending || Boolean(deployingAgentId)}
                        onClick={() => void handleDeploy(agent.worker_agent_id)}
                      >
                        <span className="worker-agent-deploy-main">
                          <strong>{agent.display_name}</strong>
                          <span>{agent.worker_agent_id}</span>
                        </span>
                        <span className="worker-agent-deploy-meta">
                          <StatusBadge value={agent.status} />
                          <span>
                            {agent.used_slots}/{agent.max_concurrent_deployments}
                          </span>
                          {deployingAgentId === agent.worker_agent_id ? (
                            <span>{t("workerEditor.deploying")}</span>
                          ) : null}
                          <span>{agent.agent_version ?? t("common.notAvailable")}</span>
                        </span>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
          </div>
        </div>
      </section>

      {isTriggerDialogOpen ? (
        <div className="ref-modal-overlay">
          <section className="ref-modal-card worker-trigger-modal">
            <header className="ref-modal-head">
              <strong>{t("workerEditor.configureTrigger")}</strong>
              <button type="button" onClick={() => setIsTriggerDialogOpen(false)}>
                {t("common.close")}
              </button>
            </header>
            <div className="ref-modal-body">
              <label className="ref-inline-control ref-inline-control-select">
                <span>{t("workerEditor.sourceType")}</span>
                <select
                  value={triggerDraft.type}
                  onChange={(e) =>
                    setTriggerDraft({
                      type: e.target.value,
                      connector_id: null,
                      fields: {},
                    })
                  }
                >
                  {sourceTypeOrder.map((typ) => (
                    <option key={typ} value={typ}>
                      {sourceTypeSchemas[typ].label}
                    </option>
                  ))}
                </select>
              </label>
              {triggerSchema?.needsConnector ? (
                <label className="ref-inline-control ref-inline-control-select">
                  <span>{t("workerEditor.availableConnector")}</span>
                  <select
                    value={triggerDraft.connector_id ?? ""}
                    onChange={(e) =>
                      setTriggerDraft({ ...triggerDraft, connector_id: e.target.value })
                    }
                  >
                    <option value="">{t("workerEditor.selectConnector")}</option>
                    {triggerConnectorOptions.map((connector) => (
                      <option key={connector.id} value={connector.id}>
                        {connector.name} ({connector.type})
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              {triggerSchema?.needsConnector && triggerConnectorOptions.length === 0 ? (
                <p className="ref-error-note">{t("workerEditor.noAvailableConnectors")}</p>
              ) : null}
              {triggerSchema?.fields.map((field) => (
                <label className="ref-inline-control" key={field.name}>
                  <span>{field.label}</span>
                  {field.type === "select" && field.options ? (
                    <select
                      value={fieldDisplayValue(triggerDraft.fields[field.name])}
                      onChange={(e) => setTriggerField(field.name, e.target.value)}
                    >
                      <option value="">{t("common.unset")}</option>
                      {field.options.map((option) => (
                        <option key={option} value={option}>
                          {option}
                        </option>
                      ))}
                    </select>
                  ) : (
                    <input
                      type={fieldInputType(field)}
                      placeholder={field.placeholder}
                      value={fieldDisplayValue(triggerDraft.fields[field.name])}
                      onChange={(e) => setTriggerField(field.name, e.target.value)}
                    />
                  )}
                </label>
              ))}
            </div>
            <footer className="ref-modal-foot">
              <button type="button" onClick={() => setIsTriggerDialogOpen(false)}>
                {t("common.cancel")}
              </button>
              <button type="button" disabled={!canApplyTrigger} onClick={applyTrigger}>
                {t("workerEditor.applyTrigger")}
              </button>
            </footer>
          </section>
        </div>
      ) : null}
    </div>
  );
}

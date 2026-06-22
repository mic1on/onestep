import { useId, useState } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { VibeActionGroup } from "../../components/ui/VibeActionGroup";
import { VibeButton } from "../../components/ui/VibeButton";
import { VibeField } from "../../components/ui/VibeField";
import { VibeModal } from "../../components/ui/VibeModal";
import { VibeSummaryStrip } from "../../components/ui/VibeSummary";
import { VibeTag, VibeTagGroup } from "../../components/ui/VibeTag";
import {
  connectorTypeOrder,
  connectorTypeSchemas,
} from "../../features/connectors/catalog";
import {
  canImportConnectorUri,
  importConnectorUri,
  type ConnectorUriImportStatus,
} from "../../features/connectors/uriImport";
import {
  useConnectorsQuery,
  useCreateConnectorMutation,
  useDeleteConnectorMutation,
  useUpdateConnectorMutation,
} from "../../features/connectors/queries";
import type { ConnectorSummary, ConnectorType } from "../../lib/api/types";
import { formatDateTime } from "../../lib/formatters";

type Draft = {
  name: string;
  config: Record<string, string>;
  secret: Record<string, string>;
};

type UriImportUiStatus = "idle" | "success" | ConnectorUriImportStatus;
type ConnectorCategory = "databases" | "queues" | "apps" | "http";

const connectorTypeCategory: Record<ConnectorType, ConnectorCategory> = {
  mysql: "databases",
  postgres: "databases",
  redis: "databases",
  rabbitmq: "queues",
  sqs: "queues",
  feishu_bitable: "apps",
  http: "http",
};

const connectorTypeGlyph: Record<ConnectorType, string> = {
  mysql: "MY",
  postgres: "PG",
  redis: "RD",
  rabbitmq: "MQ",
  sqs: "QS",
  feishu_bitable: "FB",
  http: "HT",
};

const connectorCategories: ConnectorCategory[] = ["databases", "queues", "apps", "http"];

function emptyDraft(): Draft {
  return { name: "", config: {}, secret: {} };
}

function fieldValue(draft: Draft, field: { name: string; type: string }): string {
  const source = field.type === "password" ? draft.secret : draft.config;
  return (source[field.name] as string | undefined) ?? "";
}

function uriImportMessageKey(status: UriImportUiStatus) {
  switch (status) {
    case "success":
      return "connectors.uriImportSuccess";
    case "invalid":
      return "connectors.uriImportInvalid";
    case "missing_region":
      return "connectors.uriImportMissingRegion";
    case "scheme_mismatch":
      return "connectors.uriImportSchemeMismatch";
    case "unsupported":
      return "connectors.uriImportUnsupported";
    case "idle":
    default:
      return "connectors.uriImportHint";
  }
}

export function ConnectorsPage() {
  const { t } = useTranslation();
  const uriImportInputId = useId();
  const connectorsQuery = useConnectorsQuery();
  const createMutation = useCreateConnectorMutation();
  const updateMutation = useUpdateConnectorMutation();
  const deleteMutation = useDeleteConnectorMutation();
  const [selectedType, setSelectedType] = useState<ConnectorType>("mysql");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft());
  const [editorOpen, setEditorOpen] = useState(false);
  const [uriImportValue, setUriImportValue] = useState("");
  const [uriImportStatus, setUriImportStatus] = useState<UriImportUiStatus>("idle");

  const connectors = connectorsQuery.data?.items ?? [];
  const byType = (type: ConnectorType) => connectors.filter((c) => c.type === type);
  const configuredTypeCount = connectorTypeOrder.filter((type) => byType(type).length > 0).length;
  const setupGapCount = connectorTypeOrder.length - configuredTypeCount;
  const uriImportableTypeCount = connectorTypeOrder.filter(canImportConnectorUri).length;
  const selectedSchema = connectorTypeSchemas[selectedType];
  const selectedItems = byType(selectedType);
  const editingConnector = editingId
    ? connectors.find((connector) => connector.id === editingId)
    : null;
  const canImportUri = canImportConnectorUri(selectedType);
  const uriImportMessage = t(uriImportMessageKey(uriImportStatus));
  const uriImportNoteClassName =
    uriImportStatus === "success"
      ? "connectors-uri-import-note is-success"
      : uriImportStatus === "idle"
        ? "connectors-uri-import-note"
        : "connectors-uri-import-note is-error";

  function resetUriImport() {
    setUriImportValue("");
    setUriImportStatus("idle");
  }

  function selectType(type: ConnectorType) {
    setSelectedType(type);
    setEditingId(null);
    setDraft(emptyDraft());
    setEditorOpen(false);
    resetUriImport();
  }

  function startCreate(type: ConnectorType) {
    setSelectedType(type);
    setEditingId(null);
    setDraft(emptyDraft());
    setEditorOpen(true);
    resetUriImport();
  }

  function startEdit(connector: ConnectorSummary) {
    setEditingId(connector.id);
    const config: Record<string, string> = {};
    const secret: Record<string, string> = {};
    const schema = connectorTypeSchemas[connector.type];
    for (const field of schema.fields) {
      if (field.type === "password") {
        const value = connector.secret[field.name];
        secret[field.name] = value === "****" ? "" : (value ?? "");
      } else {
        const value = connector.config[field.name];
        config[field.name] = (value as string | undefined) ?? "";
      }
    }
    setDraft({ name: connector.name, config, secret });
    setSelectedType(connector.type);
    setEditorOpen(true);
    resetUriImport();
  }

  function resetForm() {
    setEditingId(null);
    setDraft(emptyDraft());
    setEditorOpen(false);
    resetUriImport();
  }

  function handleUriImport() {
    const result = importConnectorUri(selectedType, uriImportValue);
    if (!result.ok) {
      setUriImportStatus(result.status);
      return;
    }
    setDraft((current) => ({
      name: current.name.trim() ? current.name : (result.suggestedName ?? current.name),
      config: { ...current.config, ...result.config },
      secret: { ...current.secret, ...result.secret },
    }));
    setUriImportValue("");
    setUriImportStatus("success");
  }

  async function handleSubmit(type: ConnectorType) {
    if (!draft.name.trim()) return;
    if (editingId) {
      await updateMutation.mutateAsync({
        id: editingId,
        payload: {
          name: draft.name,
          config: { ...draft.config },
          secret: { ...draft.secret },
        },
      });
    } else {
      await createMutation.mutateAsync({
        name: draft.name,
        type,
        config: { ...draft.config },
        secret: { ...draft.secret },
      });
    }
    resetForm();
  }

  return (
    <div className="ref-console-page signal-console-runtime-page connectors-page">
      <SignalConsoleHeader
        kicker={t("connectors.eyebrow")}
        title={t("connectors.title")}
        description={<p className="signal-console-hero-note">{t("connectors.subtitle")}</p>}
        side={
          <div className="signal-console-metric">
            <span>{t("connectors.summaryConnectors")}</span>
            <strong>{connectors.length}</strong>
          </div>
        }
      />

      <VibeSummaryStrip
        className="runtime-summary-strip"
        items={[
          { label: t("connectors.summaryReady"), tone: "success", value: connectors.length },
          {
            label: t("connectors.summaryConfiguredTypes"),
            tone: "success",
            value: `${configuredTypeCount}/${connectorTypeOrder.length}`,
          },
          { label: t("connectors.summarySetupGaps"), value: setupGapCount },
          { label: t("connectors.summaryUriImport"), value: `${uriImportableTypeCount}/${connectorTypeOrder.length}` },
        ]}
      />

      {connectorsQuery.error ? (
        <EmptyState
          title={t("connectors.loadErrorTitle")}
          body={String(connectorsQuery.error)}
        />
      ) : null}
      {connectorsQuery.isPending ? (
        <div className="loading-block">{t("connectors.loading")}</div>
      ) : null}

      <section className="connectors-workbench">
        <aside className="connectors-type-rail connectors-gallery" aria-label={t("connectors.typeRailLabel")}>
          <div className="connectors-gallery-head">
            <span>{t("connectors.libraryEyebrow")}</span>
            <strong>{t("connectors.libraryTitle")}</strong>
          </div>
          {connectorCategories.map((category) => (
            <section className="connectors-gallery-group" key={category}>
              <h3>{t(`connectors.category.${category}`)}</h3>
              <div className="connectors-gallery-grid">
                {connectorTypeOrder
                  .filter((type) => connectorTypeCategory[type] === category)
                  .map((type) => {
                    const schema = connectorTypeSchemas[type];
                    const items = byType(type);
                    const isSelected = selectedType === type;
                    return (
                      <button
                        className={isSelected ? "connector-type-option is-selected" : "connector-type-option"}
                        key={type}
                        type="button"
                        onClick={() => selectType(type)}
                      >
                        <span className="connector-type-glyph" aria-hidden="true">
                          {connectorTypeGlyph[type]}
                        </span>
                        <span className="connector-type-copy">
                          <strong>{schema.label}</strong>
                          <span>{t("connectors.connectedCount", { count: items.length })}</span>
                        </span>
                        <span className="connector-type-count">{items.length}</span>
                      </button>
                    );
                  })}
              </div>
            </section>
          ))}
        </aside>

        <div className="connectors-detail-surface">
          <header className="connectors-detail-head">
            <div>
              <span>{t("connectors.selectedTypeLabel")}</span>
              <h3>{selectedSchema.label}</h3>
            </div>
            <VibeTagGroup className="connectors-capability-tags">
              <VibeTag>{t("connectors.capabilitySource")}</VibeTag>
              <VibeTag>{t("connectors.capabilitySink")}</VibeTag>
              {canImportUri ? <VibeTag>{t("connectors.capabilityUri")}</VibeTag> : null}
            </VibeTagGroup>
            <VibeButton icon="+" onClick={() => startCreate(selectedType)} variant="primary">
              {t("connectors.actionNew")}
            </VibeButton>
          </header>

          <div className="connectors-detail-grid connectors-asset-grid">
            <section className="connectors-list-panel">
              <div className="connectors-panel-heading">
                <span>{t("connectors.connectedListTitle", { type: selectedSchema.label })}</span>
                <strong>{selectedItems.length}</strong>
              </div>
              <div className="connectors-selected-brief">
                <p>{t("connectors.typeBrief", { type: selectedSchema.label })}</p>
                <div className="connectors-selected-facts">
                  <span>{t("connectors.factFields", { count: selectedSchema.fields.length })}</span>
                  <span>{canImportUri ? t("connectors.factUriReady") : t("connectors.factManualOnly")}</span>
                </div>
              </div>
              {selectedItems.length === 0 ? (
                <EmptyState
                  title={t("connectors.emptyTypeTitle", { type: selectedSchema.label })}
                  body={t("connectors.emptyTypeBody", { type: selectedSchema.label })}
                  action={
                    <VibeButton icon="+" onClick={() => startCreate(selectedType)} variant="primary">
                      {t("connectors.actionNew")}
                    </VibeButton>
                  }
                />
              ) : (
                <div className="connectors-list">
                  {selectedItems.map((connector) => (
                    <div
                      className={
                        connector.id === editingId
                          ? "ref-accordion-item runtime-list-item is-selected"
                          : "ref-accordion-item runtime-list-item"
                      }
                      key={connector.id}
                    >
                      <span className="connector-instance-mark" aria-hidden="true">
                        {connectorTypeGlyph[connector.type]}
                      </span>
                      <div className="ref-meta-cell">
                        <strong>{connector.name}</strong>
                        <span>{t("connectors.createdAt", { time: formatDateTime(connector.created_at) })}</span>
                      </div>
                      <div className="connectors-instance-meta">
                        <span>{t("connectors.statusReady")}</span>
                        <span>{t("connectors.reusableBySteps")}</span>
                      </div>
                      <VibeActionGroup>
                        <VibeButton onClick={() => startEdit(connector)} variant="secondary">
                          {t("connectors.actionEdit")}
                        </VibeButton>
                        <VibeButton
                          disabled={deleteMutation.isPending}
                          onClick={() => void deleteMutation.mutateAsync(connector.id)}
                          variant="danger"
                        >
                          {t("connectors.actionDelete")}
                        </VibeButton>
                      </VibeActionGroup>
                    </div>
                  ))}
                </div>
              )}
            </section>
            <section className="connectors-playbook-panel">
              <span>{t("connectors.playbookEyebrow")}</span>
              <h4>{t("connectors.playbookTitle")}</h4>
              <div className="connectors-playbook-steps">
                <div>
                  <strong>01</strong>
                  <p>{canImportUri ? t("connectors.playbookImport") : t("connectors.playbookManual")}</p>
                </div>
                <div>
                  <strong>02</strong>
                  <p>{t("connectors.playbookName")}</p>
                </div>
                <div>
                  <strong>03</strong>
                  <p>{t("connectors.playbookReuse")}</p>
                </div>
              </div>
              <VibeButton icon="+" onClick={() => startCreate(selectedType)} variant="primary">
                {t("connectors.actionNew")}
              </VibeButton>
            </section>
          </div>
        </div>
      </section>

      <VibeModal
        bodyClassName="connectors-editor-modal-body"
        footer={
          <>
            <VibeButton onClick={resetForm} variant="secondary">
              {t("common.cancel")}
            </VibeButton>
            <VibeButton
              disabled={!draft.name.trim() || createMutation.isPending || updateMutation.isPending}
              onClick={() => void handleSubmit(selectedType)}
              variant="primary"
            >
              {editingId ? t("connectors.actionSave") : t("connectors.actionCreate")}
            </VibeButton>
          </>
        }
        onClose={resetForm}
        open={editorOpen}
        title={
          editingId
            ? t("connectors.formEditNamed", {
                name: editingConnector?.name ?? draft.name,
              })
            : t("connectors.formNew", { type: selectedSchema.label })
        }
      >
        <section className="ref-inline-form runtime-inline-form connectors-editor-form">
          <p>{t("connectors.escapeHint")}</p>
          {canImportUri ? (
            <div className="connectors-uri-import connectors-form-full">
              <label className="connectors-uri-import-label" htmlFor={uriImportInputId}>
                {t("connectors.uriImportLabel")}
              </label>
              <div className="connectors-uri-import-control-row">
                <input
                  aria-label={t("connectors.uriImportLabel")}
                  autoComplete="off"
                  className="connectors-uri-import-input"
                  id={uriImportInputId}
                  onChange={(event) => {
                    setUriImportValue(event.target.value);
                    setUriImportStatus("idle");
                  }}
                  placeholder={t("connectors.uriImportPlaceholder")}
                  type="password"
                  value={uriImportValue}
                />
                <VibeButton
                  className="connectors-uri-import-action"
                  disabled={!uriImportValue.trim()}
                  onClick={handleUriImport}
                  variant="secondary"
                >
                  {t("connectors.uriImportAction")}
                </VibeButton>
              </div>
              <p className={uriImportNoteClassName}>{uriImportMessage}</p>
            </div>
          ) : null}
          <VibeField
            autoFocus
            className="connectors-form-full"
            label={t("connectors.fieldName")}
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            type="text"
            value={draft.name}
          />
          {selectedSchema.fields.map((field) => (
            <VibeField
              key={field.name}
              label={field.label}
              onChange={(event) => {
                const target = field.type === "password" ? "secret" : "config";
                setDraft({
                  ...draft,
                  [target]: { ...draft[target], [field.name]: event.target.value },
                });
              }}
              placeholder={
                field.type === "password" && editingId
                  ? t("connectors.leaveUnchanged")
                  : field.placeholder
              }
              type={field.type === "password" ? "password" : "text"}
              value={fieldValue(draft, field)}
            />
          ))}
        </section>
      </VibeModal>
    </div>
  );
}

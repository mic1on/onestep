import { useState } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import {
  connectorTypeOrder,
  connectorTypeSchemas,
} from "../../features/connectors/catalog";
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

function emptyDraft(): Draft {
  return { name: "", config: {}, secret: {} };
}

function fieldValue(draft: Draft, field: { name: string; type: string }): string {
  const source = field.type === "password" ? draft.secret : draft.config;
  return (source[field.name] as string | undefined) ?? "";
}

export function ConnectorsPage() {
  const { t } = useTranslation();
  const connectorsQuery = useConnectorsQuery();
  const createMutation = useCreateConnectorMutation();
  const updateMutation = useUpdateConnectorMutation();
  const deleteMutation = useDeleteConnectorMutation();
  const [selectedType, setSelectedType] = useState<ConnectorType>("mysql");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft>(emptyDraft());

  const connectors = connectorsQuery.data?.items ?? [];
  const byType = (type: ConnectorType) => connectors.filter((c) => c.type === type);
  const configuredTypeCount = connectorTypeOrder.filter((type) => byType(type).length > 0).length;
  const selectedSchema = connectorTypeSchemas[selectedType];
  const selectedItems = byType(selectedType);
  const editingConnector = editingId
    ? connectors.find((connector) => connector.id === editingId)
    : null;

  function selectType(type: ConnectorType) {
    setSelectedType(type);
    setEditingId(null);
    setDraft(emptyDraft());
  }

  function startCreate(type: ConnectorType) {
    setSelectedType(type);
    setEditingId(null);
    setDraft(emptyDraft());
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
  }

  function resetForm() {
    setEditingId(null);
    setDraft(emptyDraft());
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

      <section className="ref-summary-strip runtime-summary-strip">
        <article className="ref-summary-chip ref-summary-chip-default">
          <span>{t("connectors.summaryConnectors")}</span>
          <strong>{connectors.length}</strong>
        </article>
        <article className="ref-summary-chip ref-summary-chip-success">
          <span>{t("connectors.summaryConfiguredTypes")}</span>
          <strong>
            {configuredTypeCount}/{connectorTypeOrder.length}
          </strong>
        </article>
        <article className="ref-summary-chip ref-summary-chip-default">
          <span>{t("connectors.summaryAvailableTypes")}</span>
          <strong>{connectorTypeOrder.length}</strong>
        </article>
      </section>

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
        <aside className="connectors-type-rail" aria-label={t("connectors.typeRailLabel")}>
          {connectorTypeOrder.map((type) => {
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
                <span className="connector-type-copy">
                  <strong>{schema.label}</strong>
                  <span>{t("connectors.connectedCount", { count: items.length })}</span>
                </span>
                <span className="connector-type-count">{items.length}</span>
              </button>
            );
          })}
        </aside>

        <div className="connectors-detail-surface">
          <header className="connectors-detail-head">
            <div>
              <span>{t("connectors.selectedTypeLabel")}</span>
              <h3>{selectedSchema.label}</h3>
            </div>
            <button type="button" onClick={() => startCreate(selectedType)}>
              {t("connectors.actionNew")}
            </button>
          </header>

          <div className="connectors-detail-grid">
            <section className="connectors-list-panel">
              <div className="connectors-panel-heading">
                <span>{t("connectors.connectedListTitle")}</span>
                <strong>{selectedItems.length}</strong>
              </div>
              {selectedItems.length === 0 ? (
                <div className="runtime-empty-inline">{t("connectors.emptyTypeBody")}</div>
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
                      <div className="ref-meta-cell">
                        <strong>{connector.name}</strong>
                        <span>{formatDateTime(connector.created_at)}</span>
                      </div>
                      <div className="ref-page-actions">
                        <button type="button" onClick={() => startEdit(connector)}>
                          {t("connectors.actionEdit")}
                        </button>
                        <button
                          type="button"
                          disabled={deleteMutation.isPending}
                          onClick={() => void deleteMutation.mutateAsync(connector.id)}
                        >
                          {t("connectors.actionDelete")}
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </section>

            <section className="ref-inline-form runtime-inline-form connectors-editor-form">
              <h4>
                {editingId
                  ? t("connectors.formEditNamed", {
                      name: editingConnector?.name ?? draft.name,
                    })
                  : t("connectors.formNew", { type: selectedSchema.label })}
              </h4>
              <p>{t("connectors.escapeHint")}</p>
              <label className="ref-inline-control connectors-form-full">
                <span>{t("connectors.fieldName")}</span>
                <input
                  type="text"
                  value={draft.name}
                  onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                />
              </label>
              {selectedSchema.fields.map((field) => (
                <label className="ref-inline-control" key={field.name}>
                  <span>{field.label}</span>
                  <input
                    type={field.type === "password" ? "password" : "text"}
                    placeholder={
                      field.type === "password" && editingId
                        ? t("connectors.leaveUnchanged")
                        : field.placeholder
                    }
                    value={fieldValue(draft, field)}
                    onChange={(e) => {
                      const target = field.type === "password" ? "secret" : "config";
                      setDraft({
                        ...draft,
                        [target]: { ...draft[target], [field.name]: e.target.value },
                      });
                    }}
                  />
                </label>
              ))}
              <div className="ref-page-actions connectors-form-full">
                <button
                  type="button"
                  disabled={createMutation.isPending || updateMutation.isPending}
                  onClick={() => void handleSubmit(selectedType)}
                >
                  {editingId ? t("connectors.actionSave") : t("connectors.actionCreate")}
                </button>
                {editingId ? (
                  <button type="button" onClick={resetForm}>
                    {t("common.cancel")}
                  </button>
                ) : null}
              </div>
            </section>
          </div>
        </div>
      </section>
    </div>
  );
}

import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { PageHeader } from "../../components/ui/PageHeader";
import { Panel } from "../../components/ui/Panel";
import { StatusBadge } from "../../components/ui/StatusBadge";
import {
  useCreateNotificationChannelMutation,
  useDeleteNotificationChannelMutation,
  useNotificationChannelsQuery,
  useNotificationServicesQuery,
  useTestNotificationChannelMutation,
  useUpdateNotificationChannelMutation,
} from "../../features/notifications/queries";
import type { NotificationChannel, NotificationChannelUpsertRequest, NotificationEventType, NotificationProvider, NotificationServiceScope } from "../../lib/api/types";

const EVENT_VALUES: NotificationEventType[] = [
  "task_started",
  "task_succeeded",
  "task_failed",
  "task_missed_start",
];

type FormState = {
  id: string | null;
  name: string;
  provider: NotificationProvider;
  webhook_url: string;
  enabled: boolean;
  service_scopes: NotificationServiceScope[];
  event_types: NotificationEventType[];
  missed_start_grace_seconds: string;
};

const DEFAULT_FORM_STATE: FormState = {
  id: null,
  name: "",
  provider: "feishu",
  webhook_url: "",
  enabled: true,
  service_scopes: [],
  event_types: ["task_failed"],
  missed_start_grace_seconds: "300",
};

export function SettingsNotificationsPage() {
  const { t } = useTranslation();
  const channelsQuery = useNotificationChannelsQuery();
  const servicesQuery = useNotificationServicesQuery();
  const createMutation = useCreateNotificationChannelMutation();
  const updateMutation = useUpdateNotificationChannelMutation();
  const deleteMutation = useDeleteNotificationChannelMutation();
  const testMutation = useTestNotificationChannelMutation();

  const channels = channelsQuery.data?.items ?? [];
  const availableServices = servicesQuery.data?.items ?? [];

  const [selectedChannelId, setSelectedChannelId] = useState<string | "new">("new");
  const [formState, setFormState] = useState<FormState>(DEFAULT_FORM_STATE);
  const [feedback, setFeedback] = useState<{ tone: "success" | "error"; message: string } | null>(null);

  const selectedChannel = useMemo(
    () => channels.find((channel) => channel.id === selectedChannelId) ?? null,
    [channels, selectedChannelId],
  );

  useEffect(() => {
    if (selectedChannelId === "new") {
      setFormState(DEFAULT_FORM_STATE);
      return;
    }

    if (selectedChannel) {
      setFormState(channelToFormState(selectedChannel));
      return;
    }

    if (channels.length === 0) {
      setSelectedChannelId("new");
      setFormState(DEFAULT_FORM_STATE);
    }
  }, [channels.length, selectedChannel, selectedChannelId]);

  useEffect(() => {
    if (selectedChannelId === "new") {
      return;
    }
    if (!selectedChannel && channels.length > 0) {
      setSelectedChannelId(channels[0].id);
    }
  }, [channels, selectedChannel, selectedChannelId]);

  const isSaving = createMutation.isPending || updateMutation.isPending;
  const isDeleting = deleteMutation.isPending;
  const isTesting = testMutation.isPending;
  const hasMissedStart = formState.event_types.includes("task_missed_start");

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFeedback(null);

    const payload = buildPayload(formState);

    try {
      const saved =
        formState.id === null
          ? await createMutation.mutateAsync(payload)
          : await updateMutation.mutateAsync({ channelId: formState.id, payload });

      setSelectedChannelId(saved.id);
      setFormState(channelToFormState(saved));
      setFeedback({
        tone: "success",
        message: formState.id === null ? t("notifications.created") : t("notifications.updated"),
      });
    } catch (error) {
      setFeedback({
        tone: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function handleDelete() {
    if (!formState.id) {
      setFormState(DEFAULT_FORM_STATE);
      setSelectedChannelId("new");
      setFeedback(null);
      return;
    }

    setFeedback(null);

    try {
      await deleteMutation.mutateAsync(formState.id);
      const remainingChannels = channels.filter((channel) => channel.id !== formState.id);
      setSelectedChannelId(remainingChannels[0]?.id ?? "new");
      setFormState(remainingChannels[0] ? channelToFormState(remainingChannels[0]) : DEFAULT_FORM_STATE);
      setFeedback({
        tone: "success",
        message: t("notifications.deleted"),
      });
    } catch (error) {
      setFeedback({
        tone: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function handleTest() {
    if (!formState.id) {
      setFeedback({
        tone: "error",
        message: t("notifications.saveBeforeTest"),
      });
      return;
    }

    setFeedback(null);

    try {
      const response = await testMutation.mutateAsync(formState.id);
      setFeedback({
        tone: "success",
        message: response.message ?? response.detail ?? t("notifications.testSent"),
      });
    } catch (error) {
      setFeedback({
        tone: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  return (
    <div className="ref-console-page" style={{ gap: 16 }}>
      <PageHeader
        title={t("notifications.title")}
        subtitle={<p>{t("notifications.subtitle")}</p>}
        actions={
          <div className="page-actions-stack">
            <button
              className="ref-primary-button"
              onClick={() => {
                setSelectedChannelId("new");
                setFormState(DEFAULT_FORM_STATE);
                setFeedback(null);
              }}
              type="button"
            >
              {t("notifications.newChannel")}
            </button>
          </div>
        }
      />

      {feedback ? (
        <div className={feedback.tone === "success" ? "inline-feedback inline-feedback-success" : "inline-feedback inline-feedback-error"}>
          {feedback.message}
        </div>
      ) : null}

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "minmax(280px, 360px) minmax(0, 1fr)",
          gap: 16,
          alignItems: "start",
        }}
      >
        <Panel
          title={t("notifications.channelsTitle")}
          subtitle={t("notifications.channelsSubtitle")}
          className="ref-card-panel ref-card-panel-compact"
        >
          {channelsQuery.isPending ? <div className="loading-block">{t("notifications.loadingChannels")}</div> : null}
          {channelsQuery.error ? <EmptyState title={t("notifications.loadErrorTitle")} body={String(channelsQuery.error)} /> : null}
          {!channelsQuery.isPending && !channelsQuery.error && channels.length === 0 ? (
            <EmptyState
              title={t("notifications.emptyTitle")}
              body={t("notifications.emptyBody")}
            />
          ) : null}

          {!channelsQuery.isPending && !channelsQuery.error && channels.length > 0 ? (
            <div style={{ display: "grid", gap: 10 }}>
              {channels.map((channel) => {
                const selected = channel.id === selectedChannelId;
                return (
                  <button
                    key={channel.id}
                    onClick={() => {
                      setSelectedChannelId(channel.id);
                      setFeedback(null);
                    }}
                    type="button"
                    className="ref-ghost-button"
                    style={{
                      display: "grid",
                      gap: 10,
                      justifyItems: "stretch",
                      padding: 14,
                      textAlign: "left",
                      background: selected ? "var(--ref-surface-accent)" : "var(--ref-surface-soft)",
                      borderColor: selected ? "var(--ref-border-emphasis)" : "var(--ref-border-strong)",
                    }}
                  >
                    <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                      <strong style={{ fontSize: 15 }}>{channel.name}</strong>
                      <StatusBadge
                        value={channel.enabled ? "active" : "offline"}
                        label={channel.enabled ? t("notifications.enabled") : t("notifications.disabled")}
                      />
                    </div>
                    <div style={{ display: "grid", gap: 6 }}>
                      <span style={{ color: "var(--ref-ink-700)", fontSize: 13 }}>
                        {getProviderLabel(channel.provider, t)} · {t("notifications.servicesCount", { count: channel.service_scopes.length })}
                      </span>
                      <span style={{ color: "var(--ref-ink-600)", fontSize: 12 }}>
                        {formatEventTypes(channel.event_types, t)}
                      </span>
                    </div>
                  </button>
                );
              })}
            </div>
          ) : null}
        </Panel>

        <Panel
          title={formState.id ? t("notifications.editChannel") : t("notifications.createChannel")}
          subtitle={t("notifications.formSubtitle")}
          className="ref-card-panel"
          actions={
            <div className="page-actions-inline">
              <button className="button-secondary" disabled={isTesting || isSaving} onClick={() => void handleTest()} type="button">
                {isTesting ? t("notifications.sending") : t("notifications.sendTest")}
              </button>
              <button
                className={formState.id ? "button-secondary button-danger" : "button-secondary"}
                disabled={isDeleting || isSaving}
                onClick={() => void handleDelete()}
                type="button"
              >
                {formState.id ? (isDeleting ? t("notifications.deleting") : t("notifications.delete")) : t("notifications.reset")}
              </button>
            </div>
          }
        >
          <form onSubmit={(event) => void handleSubmit(event)} style={{ display: "grid", gap: 18 }}>
            <div className="ref-overview-grid ref-overview-grid-compact">
              <label className="ref-inline-control">
                <span>{t("notifications.nameLabel")}</span>
                <input
                  onChange={(event) => setFormState((current) => ({ ...current, name: event.target.value }))}
                  placeholder={t("notifications.namePlaceholder")}
                  required
                  type="text"
                  value={formState.name}
                />
              </label>

              <label className="ref-inline-control">
                <span>{t("notifications.providerLabel")}</span>
                <select
                  onChange={(event) =>
                    setFormState((current) => ({
                      ...current,
                      provider: event.target.value as NotificationProvider,
                    }))
                  }
                  value={formState.provider}
                >
                  <option value="feishu">{t("notifications.providerFeishu")}</option>
                  <option value="wechat_work">{t("notifications.providerWecom")}</option>
                </select>
              </label>
            </div>

            <label className="ref-inline-control">
              <span>{t("notifications.webhookUrlLabel")}</span>
              <input
                onChange={(event) => setFormState((current) => ({ ...current, webhook_url: event.target.value }))}
                placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                required
                type="url"
                value={formState.webhook_url}
              />
            </label>

            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: 12,
                padding: "12px 14px",
                border: "1px solid var(--ref-border)",
                borderRadius: 14,
                background: "var(--ref-surface-subtle)",
              }}
            >
              <input
                checked={formState.enabled}
                onChange={(event) => setFormState((current) => ({ ...current, enabled: event.target.checked }))}
                type="checkbox"
              />
              <div style={{ display: "grid", gap: 4 }}>
                <strong style={{ fontSize: 14 }}>{t("notifications.enabledLabel")}</strong>
                <span style={{ color: "var(--ref-ink-600)", fontSize: 12 }}>
                  {t("notifications.enabledHint")}
                </span>
              </div>
            </label>

            <section style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "grid", gap: 4 }}>
                <strong style={{ fontSize: 15 }}>{t("notifications.servicesTitle")}</strong>
                <span style={{ color: "var(--ref-ink-600)", fontSize: 13 }}>
                  {t("notifications.servicesSubtitle")}
                </span>
              </div>
              {servicesQuery.isPending ? <div className="loading-block">{t("notifications.loadingServices")}</div> : null}
              {servicesQuery.error ? <EmptyState title={t("notifications.loadServicesErrorTitle")} body={String(servicesQuery.error)} /> : null}
              {!servicesQuery.isPending && !servicesQuery.error ? (
                <div
                  style={{
                    display: "grid",
                    gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                    gap: 10,
                  }}
                >
                  {availableServices.map((service) => {
                    const checked = hasServiceScope(formState.service_scopes, service);
                    return (
                      <label
                        key={`${service.environment}:${service.name}`}
                        style={{
                          display: "flex",
                          gap: 12,
                          alignItems: "flex-start",
                          padding: "12px 14px",
                          border: "1px solid var(--ref-border)",
                          borderRadius: 14,
                          background: checked ? "var(--ref-surface-accent)" : "var(--ref-surface-subtle)",
                        }}
                      >
                        <input
                          checked={checked}
                          onChange={() =>
                            setFormState((current) => ({
                              ...current,
                              service_scopes: toggleServiceScope(current.service_scopes, service),
                            }))
                          }
                          type="checkbox"
                        />
                        <div style={{ display: "grid", gap: 4 }}>
                          <strong style={{ fontSize: 14 }}>{service.name}</strong>
                          <span style={{ color: "var(--ref-ink-600)", fontSize: 12 }}>{service.environment}</span>
                        </div>
                      </label>
                    );
                  })}
                </div>
              ) : null}
            </section>

            <section style={{ display: "grid", gap: 10 }}>
              <div style={{ display: "grid", gap: 4 }}>
                <strong style={{ fontSize: 15 }}>{t("notifications.eventTypesTitle")}</strong>
                <span style={{ color: "var(--ref-ink-600)", fontSize: 13 }}>
                  {t("notifications.eventTypesSubtitle")}
                </span>
              </div>
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
                  gap: 10,
                }}
              >
                {EVENT_VALUES.map((value) => {
                  const checked = formState.event_types.includes(value);
                  return (
                    <label
                      key={value}
                      style={{
                        display: "flex",
                        gap: 12,
                        alignItems: "flex-start",
                        padding: "12px 14px",
                        border: "1px solid var(--ref-border)",
                        borderRadius: 14,
                        background: checked ? "var(--ref-surface-accent)" : "var(--ref-surface-subtle)",
                      }}
                    >
                      <input
                        checked={checked}
                        onChange={() =>
                          setFormState((current) => ({
                            ...current,
                            event_types: toggleEventType(current.event_types, value),
                          }))
                        }
                        type="checkbox"
                      />
                      <div style={{ display: "grid", gap: 4 }}>
                        <strong style={{ fontSize: 14 }}>{getEventLabel(value, t)}</strong>
                        <span style={{ color: "var(--ref-ink-600)", fontSize: 12 }}>{getEventDesc(value, t)}</span>
                      </div>
                    </label>
                  );
                })}
              </div>
            </section>

            {hasMissedStart ? (
              <label className="ref-inline-control" style={{ maxWidth: 260 }}>
                <span>{t("notifications.graceLabel")}</span>
                <input
                  min={1}
                  onChange={(event) =>
                    setFormState((current) => ({
                      ...current,
                      missed_start_grace_seconds: event.target.value,
                    }))
                  }
                  step={1}
                  type="number"
                  value={formState.missed_start_grace_seconds}
                />
              </label>
            ) : null}

            <div className="page-actions-inline">
              <button className="ref-primary-button" disabled={isSaving} type="submit">
                {isSaving ? t("notifications.saving") : formState.id ? t("notifications.saveChanges") : t("notifications.createChannel")}
              </button>
            </div>
          </form>
        </Panel>
      </div>
    </div>
  );
}

function channelToFormState(channel: NotificationChannel): FormState {
  return {
    id: channel.id,
    name: channel.name,
    provider: channel.provider,
    webhook_url: channel.webhook_url,
    enabled: channel.enabled,
    service_scopes: channel.service_scopes,
    event_types: channel.event_types,
    missed_start_grace_seconds: String(channel.missed_start_grace_seconds ?? 300),
  };
}

function buildPayload(formState: FormState): NotificationChannelUpsertRequest {
  const payload: NotificationChannelUpsertRequest = {
    name: formState.name.trim(),
    provider: formState.provider,
    webhook_url: formState.webhook_url.trim(),
    enabled: formState.enabled,
    service_scopes: [...formState.service_scopes].sort(compareServiceScopes),
    event_types: [...formState.event_types],
  };

  if (formState.event_types.includes("task_missed_start")) {
    payload.missed_start_grace_seconds = normalizeGraceSeconds(formState.missed_start_grace_seconds);
  }

  return payload;
}

function normalizeGraceSeconds(rawValue: string) {
  const parsed = Number.parseInt(rawValue, 10);
  if (!Number.isFinite(parsed) || parsed <= 0) {
    return 300;
  }
  return parsed;
}

function toggleServiceScope(
  currentScopes: NotificationServiceScope[],
  targetScope: NotificationServiceScope,
) {
  const exists = hasServiceScope(currentScopes, targetScope);
  if (exists) {
    return currentScopes.filter(
      (scope) => !(scope.name === targetScope.name && scope.environment === targetScope.environment),
    );
  }
  return [...currentScopes, targetScope].sort(compareServiceScopes);
}

function hasServiceScope(currentScopes: NotificationServiceScope[], targetScope: NotificationServiceScope) {
  return currentScopes.some(
    (scope) => scope.name === targetScope.name && scope.environment === targetScope.environment,
  );
}

function compareServiceScopes(left: NotificationServiceScope, right: NotificationServiceScope) {
  if (left.environment !== right.environment) {
    return left.environment.localeCompare(right.environment);
  }
  return left.name.localeCompare(right.name);
}

function toggleEventType(currentTypes: NotificationEventType[], targetType: NotificationEventType) {
  if (currentTypes.includes(targetType)) {
    return currentTypes.filter((eventType) => eventType !== targetType);
  }
  return [...currentTypes, targetType];
}

const EVENT_LABEL_KEYS: Record<NotificationEventType, string> = {
  task_started: "notifications.eventTaskStarted",
  task_succeeded: "notifications.eventTaskSucceeded",
  task_failed: "notifications.eventTaskFailed",
  task_missed_start: "notifications.eventTaskMissedStart",
};

const EVENT_DESC_KEYS: Record<NotificationEventType, string> = {
  task_started: "notifications.eventTaskStartedDesc",
  task_succeeded: "notifications.eventTaskSucceededDesc",
  task_failed: "notifications.eventTaskFailedDesc",
  task_missed_start: "notifications.eventTaskMissedStartDesc",
};

function getEventLabel(eventType: NotificationEventType, t: (key: string) => string) {
  return t(EVENT_LABEL_KEYS[eventType]);
}

function getEventDesc(eventType: NotificationEventType, t: (key: string) => string) {
  return t(EVENT_DESC_KEYS[eventType]);
}

function getProviderLabel(provider: NotificationProvider, t: (key: string) => string) {
  return provider === "feishu" ? t("notifications.providerFeishu") : t("notifications.providerWecom");
}

function formatEventTypes(eventTypes: NotificationEventType[], t: (key: string) => string) {
  if (eventTypes.length === 0) {
    return t("notifications.noEventsSelected");
  }

  return eventTypes.map((eventType) => getEventLabel(eventType, t)).join(", ");
}

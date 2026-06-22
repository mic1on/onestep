import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";

import { EmptyState } from "../../components/ui/EmptyState";
import { Panel } from "../../components/ui/Panel";
import { SegmentedControl } from "../../components/ui/SegmentedControl";
import { SignalConsoleHeader } from "../../components/ui/SignalConsoleHeader";
import { useToast } from "../../components/ui/ToastProvider";
import { VibeActionGroup } from "../../components/ui/VibeActionGroup";
import { VibeButton } from "../../components/ui/VibeButton";
import { VibeChoiceCard } from "../../components/ui/VibeChoiceCard";
import { VibeField } from "../../components/ui/VibeField";
import { VibePopconfirm } from "../../components/ui/VibePopconfirm";
import { VibeSelectableListItem } from "../../components/ui/VibeSelectableListItem";
import { VibeSummaryStrip } from "../../components/ui/VibeSummary";
import { VibeSwitch } from "../../components/ui/VibeSwitch";
import { VibehubSelect } from "../../components/ui/VibehubSelect";
import { canManageNotificationSettings } from "../../features/auth/session";
import { useConsoleSessionQuery } from "../../features/auth/queries";
import {
  useCreateNotificationChannelMutation,
  useDeleteNotificationChannelMutation,
  useNotificationChannelsQuery,
  useNotificationServicesQuery,
  useTestNotificationChannelMutation,
  useUpdateNotificationChannelMutation,
} from "../../features/notifications/queries";
import type {
  Environment,
  NotificationChannel,
  NotificationChannelPatchRequest,
  NotificationChannelUpsertRequest,
  NotificationEventType,
  NotificationProvider,
  NotificationServiceScope,
} from "../../lib/api/types";

const EVENT_VALUES: NotificationEventType[] = [
  "task_started",
  "task_succeeded",
  "task_failed",
  "task_missed_start",
  "instance_online",
  "instance_offline",
];
const SERVICE_ENVIRONMENT_FILTERS = ["all", "prod", "staging", "dev"] as const;
const DEFAULT_SERVICE_ENVIRONMENT_FILTER: ServiceEnvironmentFilter = "prod";

type ServiceEnvironmentFilter = (typeof SERVICE_ENVIRONMENT_FILTERS)[number];

type FormState = {
  id: string | null;
  name: string;
  provider: NotificationProvider;
  webhook_url: string;
  webhook_url_masked: string | null;
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
  webhook_url_masked: null,
  enabled: true,
  service_scopes: [],
  event_types: ["task_failed"],
  missed_start_grace_seconds: "300",
};

export function SettingsNotificationsPage() {
  const { t } = useTranslation();
  const { pushToast } = useToast();
  const sessionQuery = useConsoleSessionQuery();
  const channelsQuery = useNotificationChannelsQuery();
  const servicesQuery = useNotificationServicesQuery();
  const createMutation = useCreateNotificationChannelMutation();
  const updateMutation = useUpdateNotificationChannelMutation();
  const deleteMutation = useDeleteNotificationChannelMutation();
  const testMutation = useTestNotificationChannelMutation();

  const channels = channelsQuery.data?.items ?? [];
  const firstChannelId = channels[0]?.id ?? null;
  const availableServices = servicesQuery.data?.items ?? [];
  const canManageNotifications = canManageNotificationSettings(sessionQuery.data);
  const enabledChannelCount = channels.filter((channel) => channel.enabled).length;
  const coveredServiceCount = useMemo(
    () =>
      new Set(
        channels.flatMap((channel) =>
          channel.service_scopes.map((scope) => `${scope.environment}:${scope.name}`),
        ),
      ).size,
    [channels],
  );
  const eventRouteCount = channels.reduce((total, channel) => total + channel.event_types.length, 0);

  const [selectedChannelId, setSelectedChannelId] = useState<string | "new" | null>(null);
  const [serviceEnvironmentFilter, setServiceEnvironmentFilter] = useState<ServiceEnvironmentFilter>(DEFAULT_SERVICE_ENVIRONMENT_FILTER);
  const [formState, setFormState] = useState<FormState>(DEFAULT_FORM_STATE);
  const [isDeleteConfirmOpen, setIsDeleteConfirmOpen] = useState(false);

  const selectedChannel = useMemo(
    () => channels.find((channel) => channel.id === selectedChannelId) ?? null,
    [channels, selectedChannelId],
  );
  const visibleServices = useMemo(
    () =>
      availableServices.filter(
        (service) =>
          serviceEnvironmentFilter === "all" || service.environment === serviceEnvironmentFilter,
      ),
    [availableServices, serviceEnvironmentFilter],
  );
  const hiddenSelectedServiceCount = useMemo(
    () =>
      serviceEnvironmentFilter === "all"
        ? 0
        : formState.service_scopes.filter((scope) => scope.environment !== serviceEnvironmentFilter).length,
    [formState.service_scopes, serviceEnvironmentFilter],
  );

  useEffect(() => {
    if (selectedChannelId === null) {
      if (channelsQuery.isPending) {
        return;
      }
      setSelectedChannelId(firstChannelId ?? "new");
      return;
    }

    if (selectedChannelId === "new") {
      setFormState(DEFAULT_FORM_STATE);
      setServiceEnvironmentFilter(DEFAULT_SERVICE_ENVIRONMENT_FILTER);
      return;
    }

    if (selectedChannel) {
      setFormState(channelToFormState(selectedChannel));
      setServiceEnvironmentFilter(getInitialServiceEnvironmentFilter(selectedChannel.service_scopes));
      return;
    }

    if (channels.length === 0) {
      setSelectedChannelId("new");
      setFormState(DEFAULT_FORM_STATE);
      setServiceEnvironmentFilter(DEFAULT_SERVICE_ENVIRONMENT_FILTER);
    }
  }, [channels.length, channelsQuery.isPending, firstChannelId, selectedChannel, selectedChannelId]);

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
  const togglingChannelId = updateMutation.isPending ? updateMutation.variables?.channelId ?? null : null;
  const routeServicePreview = formState.service_scopes.slice(0, 4);
  const routeServiceOverflowCount = Math.max(formState.service_scopes.length - routeServicePreview.length, 0);
  const routeEventPreview = formState.event_types.slice(0, 4);
  const routeEventOverflowCount = Math.max(formState.event_types.length - routeEventPreview.length, 0);
  const webhookTargetLabel =
    formState.webhook_url.trim() ||
    formState.webhook_url_masked ||
    (formState.id ? t("notifications.webhookStoredTarget") : t("notifications.webhookDraftTarget"));

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    try {
      const saved =
        formState.id === null
          ? await createMutation.mutateAsync(buildCreatePayload(formState))
          : await updateMutation.mutateAsync({
              channelId: formState.id,
              payload: buildUpdatePayload(formState),
            });

      setSelectedChannelId(saved.id);
      setFormState(channelToFormState(saved));
      setIsDeleteConfirmOpen(false);
      pushToast({
        tone: "success",
        message: formState.id === null ? t("notifications.created") : t("notifications.updated"),
      });
    } catch (error) {
      pushToast({
        tone: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function handleDelete() {
    if (!formState.id) {
      setFormState(DEFAULT_FORM_STATE);
      setSelectedChannelId("new");
      setIsDeleteConfirmOpen(false);
      return;
    }

    try {
      await deleteMutation.mutateAsync(formState.id);
      const remainingChannels = channels.filter((channel) => channel.id !== formState.id);
      setSelectedChannelId(remainingChannels[0]?.id ?? "new");
      setFormState(remainingChannels[0] ? channelToFormState(remainingChannels[0]) : DEFAULT_FORM_STATE);
      setIsDeleteConfirmOpen(false);
      pushToast({
        tone: "success",
        message: t("notifications.deleted"),
      });
    } catch (error) {
      pushToast({
        tone: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function handleTest() {
    if (!formState.id) {
      pushToast({
        tone: "error",
        message: t("notifications.saveBeforeTest"),
      });
      return;
    }

    try {
      const response = await testMutation.mutateAsync(formState.id);
      pushToast({
        tone: "success",
        message: response.message ?? response.detail ?? t("notifications.testSent"),
      });
    } catch (error) {
      pushToast({
        tone: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  async function handleToggleEnabled(channel: NotificationChannel) {
    try {
      const updated = await updateMutation.mutateAsync({
        channelId: channel.id,
        payload: { enabled: !channel.enabled },
      });
      if (selectedChannelId === updated.id) {
        setFormState(channelToFormState(updated));
      }
      pushToast({
        tone: "success",
        message: t("notifications.updated"),
      });
    } catch (error) {
      pushToast({
        tone: "error",
        message: error instanceof Error ? error.message : String(error),
      });
    }
  }

  function startNewChannel() {
    setSelectedChannelId("new");
    setFormState(DEFAULT_FORM_STATE);
    setServiceEnvironmentFilter(DEFAULT_SERVICE_ENVIRONMENT_FILTER);
    setIsDeleteConfirmOpen(false);
  }

  return (
    <div className="ref-console-page settings-notifications-page signal-console-settings-page">
      <SignalConsoleHeader
        className="signal-console-settings-hero"
        description={<p className="signal-console-hero-note">{t("notifications.subtitle")}</p>}
        kicker={t("notifications.eyebrow")}
        secondary={
          !canManageNotifications ? (
            <p className="signal-console-hero-note signal-console-settings-readonly">{t("notifications.readOnlyHint")}</p>
          ) : null
        }
        side={
          canManageNotifications ? (
            <div className="signal-console-hero-actions signal-console-header-actions signal-console-settings-hero-actions">
              <VibeActionGroup>
                <VibeButton
                  icon="+"
                  onClick={startNewChannel}
                  variant="primary"
                >
                  {t("notifications.newChannel")}
                </VibeButton>
              </VibeActionGroup>
            </div>
          ) : null
        }
        title={t("notifications.title")}
      />

      <VibeSummaryStrip
        className="signal-console-settings-band"
        items={[
          { label: t("notifications.routeEndpoints"), value: channels.length },
          { label: t("notifications.enabled"), tone: "success", value: enabledChannelCount },
          { label: t("notifications.coveredServices"), tone: "accent", value: coveredServiceCount },
          { label: t("notifications.eventRoutes"), value: eventRouteCount },
        ]}
      />

      <div className="notification-settings-layout">
        <Panel
          title={t("notifications.libraryTitle")}
          subtitle={t("notifications.librarySubtitle")}
          className="ref-card-panel ref-card-panel-compact notification-channel-panel"
        >
          {channelsQuery.isPending ? <div className="loading-block">{t("notifications.loadingChannels")}</div> : null}
          {channelsQuery.error ? <EmptyState title={t("notifications.loadErrorTitle")} body={String(channelsQuery.error)} /> : null}
          {!channelsQuery.isPending && !channelsQuery.error && channels.length === 0 ? (
            <EmptyState
              title={t("notifications.emptyTitle")}
              body={t("notifications.emptyBody")}
              action={
                canManageNotifications ? (
                  <VibeButton icon="+" onClick={startNewChannel} variant="primary">
                    {t("notifications.newChannel")}
                  </VibeButton>
                ) : null
              }
            />
          ) : null}

          {!channelsQuery.isPending && !channelsQuery.error && channels.length > 0 ? (
            <div className="notification-channel-list">
              {channels.map((channel) => {
                const selected = channel.id === selectedChannelId;
                const isToggling = togglingChannelId === channel.id;
                return (
                  <VibeSelectableListItem
                    actions={
                      <VibeSwitch
                        checked={channel.enabled}
                        disabled={!canManageNotifications || isToggling}
                        label={
                          isToggling
                            ? channel.enabled
                              ? t("notifications.disabling")
                              : t("notifications.enabling")
                            : channel.enabled
                              ? t("notifications.disable")
                              : t("notifications.enable")
                        }
                        onClick={() => void handleToggleEnabled(channel)}
                        pending={isToggling}
                      />
                    }
                    key={channel.id}
                    meta={
                      <>
                        <span>
                          {getProviderLabel(channel.provider, t)} · {t("notifications.servicesCount", { count: channel.service_scopes.length })}
                        </span>
                        <span>{formatEventTypes(channel.event_types, t)}</span>
                      </>
                    }
                    onSelect={() => {
                      setSelectedChannelId(channel.id);
                      setIsDeleteConfirmOpen(false);
                    }}
                    selected={selected}
                    title={
                      <span className="notification-channel-title-content">
                        <span className="notification-provider-mark" aria-hidden="true">
                          {getProviderGlyph(channel.provider)}
                        </span>
                        <span>{channel.name}</span>
                      </span>
                    }
                  />
                );
              })}
            </div>
          ) : null}
        </Panel>

        <Panel
          title={formState.id ? formState.name || t("notifications.editChannel") : t("notifications.createChannel")}
          subtitle={t("notifications.routePanelSubtitle")}
          className="ref-card-panel notification-route-panel"
          actions={
            <VibeActionGroup className="notification-actions-row" variant="inline">
              <VibeButton
                disabled={!canManageNotifications || isTesting || isSaving}
                onClick={() => void handleTest()}
                variant="secondary"
              >
                {isTesting ? t("notifications.sending") : t("notifications.sendTest")}
              </VibeButton>
              <VibePopconfirm
                body={t("notifications.deleteConfirmBody")}
                cancelDisabled={isDeleting}
                cancelLabel={t("notifications.cancelDelete")}
                confirmDisabled={isDeleting}
                confirmLabel={isDeleting ? t("notifications.deleting") : t("notifications.confirmDelete")}
                onCancel={() => setIsDeleteConfirmOpen(false)}
                onConfirm={() => void handleDelete()}
                open={Boolean(formState.id && isDeleteConfirmOpen)}
                title={t("notifications.deleteConfirmTitle")}
              >
                <VibeButton
                  disabled={!canManageNotifications || isDeleting || isSaving}
                  onClick={() => {
                    if (!formState.id) {
                      void handleDelete();
                      return;
                    }
                    setIsDeleteConfirmOpen((current) => !current);
                  }}
                  variant={formState.id ? "danger" : "secondary"}
                >
                  {formState.id ? t("notifications.delete") : t("notifications.reset")}
                </VibeButton>
              </VibePopconfirm>
            </VibeActionGroup>
          }
        >
          <section className="notification-route-overview">
            <div className="notification-route-hero">
              <span className="notification-provider-mark notification-provider-mark-large" aria-hidden="true">
                {getProviderGlyph(formState.provider)}
              </span>
              <div className="notification-route-hero-copy">
                <span>{t("notifications.routeOverviewLabel")}</span>
                <strong>{getProviderLabel(formState.provider, t)}</strong>
                <p>{formState.enabled ? t("notifications.routeEnabledHint") : t("notifications.routeDisabledHint")}</p>
              </div>
              <span className={formState.enabled ? "notification-route-status is-enabled" : "notification-route-status"}>
                {formState.enabled ? t("notifications.enabled") : t("notifications.disabled")}
              </span>
            </div>

            <div className="notification-route-facts">
              <article>
                <span>{t("notifications.webhookTargetTitle")}</span>
                <strong>{webhookTargetLabel}</strong>
              </article>
              <article>
                <span>{t("notifications.servicesTitle")}</span>
                <strong>{t("notifications.servicesCount", { count: formState.service_scopes.length })}</strong>
              </article>
              <article>
                <span>{t("notifications.eventTypesTitle")}</span>
                <strong>{formState.event_types.length || t("notifications.noEventsSelected")}</strong>
              </article>
            </div>

            <div className="notification-route-preview-grid">
              <div className="notification-route-preview">
                <span>{t("notifications.routeServicesPreview")}</span>
                <div className="notification-route-token-list">
                  {routeServicePreview.length > 0 ? (
                    routeServicePreview.map((scope) => (
                      <span key={`${scope.environment}:${scope.name}`}>
                        {t(`environment.${scope.environment}`)} / {scope.name}
                      </span>
                    ))
                  ) : (
                    <em>{t("notifications.noServicesSelected")}</em>
                  )}
                  {routeServiceOverflowCount > 0 ? <span>+{routeServiceOverflowCount}</span> : null}
                </div>
              </div>
              <div className="notification-route-preview">
                <span>{t("notifications.routeEventsPreview")}</span>
                <div className="notification-route-token-list">
                  {routeEventPreview.length > 0 ? (
                    routeEventPreview.map((eventType) => (
                      <span key={eventType}>{getEventLabel(eventType, t)}</span>
                    ))
                  ) : (
                    <em>{t("notifications.noEventsSelected")}</em>
                  )}
                  {routeEventOverflowCount > 0 ? <span>+{routeEventOverflowCount}</span> : null}
                </div>
              </div>
            </div>
          </section>

          <form className="notification-settings-form" onSubmit={(event) => void handleSubmit(event)}>
            <section className="notification-form-section notification-destination-section">
              <div className="notification-section-heading">
                <strong>{t("notifications.destinationTitle")}</strong>
                <span>{t("notifications.destinationSubtitle")}</span>
              </div>
              <div className="notification-form-grid">
                <VibeField
                  disabled={!canManageNotifications}
                  label={t("notifications.nameLabel")}
                  onChange={(event) => setFormState((current) => ({ ...current, name: event.target.value }))}
                  placeholder={t("notifications.namePlaceholder")}
                  required
                  type="text"
                  value={formState.name}
                />

                <VibehubSelect
                  disabled={!canManageNotifications}
                  label={t("notifications.providerLabel")}
                  onChange={(nextValue) =>
                    setFormState((current) => ({
                      ...current,
                      provider: nextValue as NotificationProvider,
                    }))
                  }
                  options={[
                    { value: "feishu", label: t("notifications.providerFeishu") },
                    { value: "wechat_work", label: t("notifications.providerWecom") },
                  ]}
                  value={formState.provider}
                />
              </div>

              <VibeField
                disabled={!canManageNotifications}
                label={t("notifications.webhookUrlLabel")}
                note={
                  formState.id && formState.webhook_url_masked
                    ? t("notifications.webhookConfiguredHint", {
                        masked: formState.webhook_url_masked,
                      })
                    : null
                }
                noteClassName="notification-services-filter-note"
                onChange={(event) => setFormState((current) => ({ ...current, webhook_url: event.target.value }))}
                placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
                required={formState.id === null}
                type="url"
                value={formState.webhook_url}
              />

              <VibeChoiceCard
                checked={formState.enabled}
                description={t("notifications.enabledHint")}
                disabled={!canManageNotifications}
                onChange={(event) => setFormState((current) => ({ ...current, enabled: event.target.checked }))}
                title={t("notifications.enabledLabel")}
                toggle
              />
            </section>

            <section className="notification-form-section">
              <div className="notification-section-heading">
                <strong>{t("notifications.servicesTitle")}</strong>
                <span>
                  {t("notifications.servicesSubtitle")}
                </span>
              </div>
              <div className="notification-services-filter">
                <span className="list-row-label">{t("notifications.servicesEnvironmentLabel")}</span>
                <SegmentedControl<ServiceEnvironmentFilter>
                  ariaLabel={t("notifications.servicesFilterAriaLabel")}
                  onChange={setServiceEnvironmentFilter}
                  options={SERVICE_ENVIRONMENT_FILTERS.map((value) => ({
                    label: t(`environment.${value}`),
                    value,
                  }))}
                  value={serviceEnvironmentFilter}
                />
                <p className="notification-services-filter-note">
                  {serviceEnvironmentFilter === "all"
                    ? t("notifications.servicesFilterSummaryAll", { count: visibleServices.length })
                    : t("notifications.servicesFilterSummaryScoped", {
                        count: visibleServices.length,
                        environment: t(`environment.${serviceEnvironmentFilter}`),
                      })}
                </p>
                {hiddenSelectedServiceCount > 0 ? (
                  <p className="notification-services-filter-note">
                    {t("notifications.servicesHiddenSelectionHint", { count: hiddenSelectedServiceCount })}
                  </p>
                ) : null}
              </div>
              {servicesQuery.isPending ? <div className="loading-block">{t("notifications.loadingServices")}</div> : null}
              {servicesQuery.error ? <EmptyState title={t("notifications.loadServicesErrorTitle")} body={String(servicesQuery.error)} /> : null}
              {!servicesQuery.isPending && !servicesQuery.error ? (
                visibleServices.length > 0 ? (
                  <div className="notification-choice-grid notification-choice-grid-services">
                    {visibleServices.map((service) => {
                      const checked = hasServiceScope(formState.service_scopes, service);
                      return (
                        <VibeChoiceCard
                          key={`${service.environment}:${service.name}`}
                          checked={checked}
                          description={service.environment}
                          disabled={!canManageNotifications}
                          onChange={() =>
                            setFormState((current) => ({
                              ...current,
                              service_scopes: toggleServiceScope(current.service_scopes, service),
                            }))
                          }
                          title={service.name}
                        />
                      );
                    })}
                  </div>
                ) : (
                  <p className="notification-services-filter-note">{t("notifications.noServicesInFilter")}</p>
                )
              ) : null}
            </section>

            <section className="notification-form-section">
              <div className="notification-section-heading">
                <strong>{t("notifications.eventTypesTitle")}</strong>
                <span>
                  {t("notifications.eventTypesSubtitle")}
                </span>
              </div>
              <div className="notification-choice-grid notification-choice-grid-events">
                {EVENT_VALUES.map((value) => {
                  const checked = formState.event_types.includes(value);
                  return (
                    <VibeChoiceCard
                      key={value}
                      checked={checked}
                      description={getEventDesc(value, t)}
                      disabled={!canManageNotifications}
                      onChange={() =>
                        setFormState((current) => ({
                          ...current,
                          event_types: toggleEventType(current.event_types, value),
                        }))
                      }
                      title={getEventLabel(value, t)}
                    />
                  );
                })}
              </div>
            </section>

            {hasMissedStart ? (
              <VibeField
                className="notification-grace-control"
                disabled={!canManageNotifications}
                label={t("notifications.graceLabel")}
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
            ) : null}

            <VibeActionGroup className="notification-actions-row" variant="inline">
              <VibeButton
                disabled={!canManageNotifications || isSaving}
                type="submit"
                variant="primary"
              >
                {isSaving ? t("notifications.saving") : formState.id ? t("notifications.saveChanges") : t("notifications.createChannel")}
              </VibeButton>
            </VibeActionGroup>
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
    webhook_url: "",
    webhook_url_masked: channel.webhook_url_masked,
    enabled: channel.enabled,
    service_scopes: channel.service_scopes,
    event_types: channel.event_types,
    missed_start_grace_seconds: String(channel.missed_start_grace_seconds ?? 300),
  };
}

function buildCreatePayload(formState: FormState): NotificationChannelUpsertRequest {
  return {
    name: formState.name.trim(),
    provider: formState.provider,
    webhook_url: formState.webhook_url.trim(),
    enabled: formState.enabled,
    service_scopes: [...formState.service_scopes].sort(compareServiceScopes),
    event_types: [...formState.event_types],
    missed_start_grace_seconds: buildMissedStartGraceSeconds(formState),
  };
}

function buildUpdatePayload(formState: FormState): NotificationChannelPatchRequest {
  const payload: NotificationChannelPatchRequest = {
    name: formState.name.trim(),
    provider: formState.provider,
    enabled: formState.enabled,
    service_scopes: [...formState.service_scopes].sort(compareServiceScopes),
    event_types: [...formState.event_types],
    missed_start_grace_seconds: buildMissedStartGraceSeconds(formState),
  };
  const webhookUrl = formState.webhook_url.trim();
  if (webhookUrl) {
    payload.webhook_url = webhookUrl;
  }
  return payload;
}

function buildMissedStartGraceSeconds(formState: FormState) {
  if (formState.event_types.includes("task_missed_start")) {
    return normalizeGraceSeconds(formState.missed_start_grace_seconds);
  }
  return 300;
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
  instance_online: "notifications.eventInstanceOnline",
  instance_offline: "notifications.eventInstanceOffline",
};

const EVENT_DESC_KEYS: Record<NotificationEventType, string> = {
  task_started: "notifications.eventTaskStartedDesc",
  task_succeeded: "notifications.eventTaskSucceededDesc",
  task_failed: "notifications.eventTaskFailedDesc",
  task_missed_start: "notifications.eventTaskMissedStartDesc",
  instance_online: "notifications.eventInstanceOnlineDesc",
  instance_offline: "notifications.eventInstanceOfflineDesc",
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

function getProviderGlyph(provider: NotificationProvider) {
  return provider === "feishu" ? "FS" : "WX";
}

function formatEventTypes(eventTypes: NotificationEventType[], t: (key: string) => string) {
  if (eventTypes.length === 0) {
    return t("notifications.noEventsSelected");
  }

  return eventTypes.map((eventType) => getEventLabel(eventType, t)).join(", ");
}

function getInitialServiceEnvironmentFilter(serviceScopes: NotificationServiceScope[]): ServiceEnvironmentFilter {
  const firstEnvironment = serviceScopes[0]?.environment;
  return firstEnvironment ?? DEFAULT_SERVICE_ENVIRONMENT_FILTER;
}

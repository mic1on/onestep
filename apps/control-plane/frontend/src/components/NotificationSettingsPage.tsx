import { type ChangeEvent, FormEvent, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Bell,
  Braces,
  Check,
  CheckCircle2,
  Edit3,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  X,
} from 'lucide-react';
import {
  createNotificationChannel,
  deleteNotificationChannel,
  getApiErrorMessage,
  isAuthRequiredError,
  listNotificationChannels,
  listNotificationServices,
  setNotificationChannelEnabled,
  testNotificationChannel,
  updateNotificationChannel,
  type NotificationChannel,
  type NotificationCustomParam,
  type NotificationWebhookMethod,
  type NotificationChannelInput,
  type NotificationEventType,
  type NotificationProvider,
  type NotificationServiceScope,
} from '../api';
import { type MessageKey, useI18n } from '../i18n';

interface NotificationSettingsPageProps {
  onAuthRequired: () => void;
  onNotify: (message: string, type?: 'success' | 'info' | 'warn') => void;
}

type FormState = {
  id: string | null;
  name: string;
  provider: NotificationProvider;
  originalProvider: NotificationProvider | null;
  webhookUrl: string;
  serviceScopeKeys: string[];
  eventTypes: NotificationEventType[];
  missedStartGraceSeconds: string;
  customMethod: NotificationWebhookMethod;
  queryParams: NotificationCustomParam[];
  bodyParams: NotificationCustomParam[];
};

const PROVIDER_VALUES: NotificationProvider[] = ['feishu', 'wechat_work', 'custom'];
const WEBHOOK_METHOD_VALUES: NotificationWebhookMethod[] = ['GET', 'POST'];
const CUSTOM_VARIABLES = [
  'event_type',
  'service_name',
  'service_environment',
  'task_name',
  'occurred_at',
  'scheduled_at',
  'duration_ms',
  'attempts',
  'instance_id',
  'node_name',
  'console_url',
  'failure_message',
  'success_summary',
] as const;

type Translate = ReturnType<typeof useI18n>['t'];

function providerLabelKey(provider: NotificationProvider) {
  return `notifications.provider.${provider}` as const;
}

const EVENTS: Array<{ value: NotificationEventType; label: string }> = [
  { value: 'task_started', label: 'Task started' },
  { value: 'task_succeeded', label: 'Task succeeded' },
  { value: 'task_failed', label: 'Task failed' },
  { value: 'task_missed_start', label: 'Task missed start' },
  { value: 'instance_online', label: 'Instance online' },
  { value: 'instance_offline', label: 'Instance offline' },
];

const DEFAULT_EVENTS: NotificationEventType[] = ['task_failed', 'task_missed_start', 'instance_offline'];

const EMPTY_FORM: FormState = {
  id: null,
  name: '',
  provider: 'feishu',
  originalProvider: null,
  webhookUrl: '',
  serviceScopeKeys: [],
  eventTypes: DEFAULT_EVENTS,
  missedStartGraceSeconds: '300',
  customMethod: 'POST',
  queryParams: [],
  bodyParams: [],
};

function providerLabel(provider: NotificationProvider, t: Translate) {
  return t(providerLabelKey(provider));
}

function eventLabel(eventType: NotificationEventType) {
  return EVENTS.find((item) => item.value === eventType)?.label ?? eventType;
}

interface StyledCheckboxProps {
  checked: boolean;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
}

function StyledCheckbox({ checked, onChange }: StyledCheckboxProps) {
  return (
    <span className="relative grid h-5 w-5 shrink-0 place-items-center">
      <input
        checked={checked}
        className="peer h-5 w-5 cursor-pointer appearance-none rounded-md border border-slate-300 bg-white shadow-xs transition-colors duration-150 checked:border-indigo-600 checked:bg-indigo-600 hover:border-indigo-300 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-100 focus-visible:ring-offset-1"
        onChange={onChange}
        type="checkbox"
      />
      <Check
        aria-hidden="true"
        className="pointer-events-none absolute h-3.5 w-3.5 stroke-[3] text-white opacity-0 transition-opacity duration-150 peer-checked:opacity-100"
      />
    </span>
  );
}

function scopeKey(scope: NotificationServiceScope) {
  return `${scope.environment}:${scope.name}`;
}

function scopeFromKey(key: string): NotificationServiceScope {
  const [environment, ...nameParts] = key.split(':');
  return {
    environment: environment as NotificationServiceScope['environment'],
    name: nameParts.join(':'),
  };
}

function uniqueScopes(scopes: NotificationServiceScope[]) {
  const seen = new Set<string>();
  return scopes.filter((scope) => {
    const key = scopeKey(scope);
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function normalizeParams(params: NotificationCustomParam[]) {
  return params
    .map((param) => ({ key: param.key.trim(), value: param.value.trim() }))
    .filter((param) => param.key || param.value);
}

function channelToForm(channel: NotificationChannel): FormState {
  return {
    id: channel.id,
    name: channel.name,
    provider: channel.provider,
    originalProvider: channel.provider,
    webhookUrl: '',
    serviceScopeKeys: channel.service_scopes.map(scopeKey),
    eventTypes: channel.event_types,
    missedStartGraceSeconds: String(channel.missed_start_grace_seconds),
    customMethod: channel.custom_config?.method ?? 'POST',
    queryParams: channel.custom_config?.query_params ?? [],
    bodyParams: channel.custom_config?.body_params ?? [],
  };
}

function channelScopeText(channel: NotificationChannel) {
  if (channel.service_scopes.length === 0) return 'All services';
  if (channel.service_scopes.length === 1) {
    const [scope] = channel.service_scopes;
    return `${scope.environment}/${scope.name}`;
  }
  return `${channel.service_scopes.length} services`;
}

export default function NotificationSettingsPage({
  onAuthRequired,
  onNotify,
}: NotificationSettingsPageProps) {
  const { t } = useI18n();
  const [channels, setChannels] = useState<NotificationChannel[]>([]);
  const [services, setServices] = useState<NotificationServiceScope[]>([]);
  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [isLoading, setIsLoading] = useState(false);
  const [isSaving, setIsSaving] = useState(false);
  const [enabledToggleId, setEnabledToggleId] = useState<string | null>(null);
  const [testingChannelId, setTestingChannelId] = useState<string | null>(null);
  const [deletingChannelId, setDeletingChannelId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [openVariablePicker, setOpenVariablePicker] = useState<string | null>(null);

  const isEditing = form.id !== null;
  const includesMissedStart = form.eventTypes.includes('task_missed_start');
  const selectedServiceKeys = new Set(form.serviceScopeKeys);
  const allServicesSelected = form.serviceScopeKeys.length === 0;
  const serviceOptions = useMemo(() => uniqueScopes(services), [services]);

  const localEventLabel = (eventType: NotificationEventType) => {
    if (eventType === 'task_started') return t('event.taskStarted');
    if (eventType === 'task_succeeded') return t('event.taskSucceeded');
    if (eventType === 'task_failed') return t('event.taskFailed');
    if (eventType === 'task_missed_start') return t('event.taskMissedStart');
    if (eventType === 'instance_online') return t('event.instanceOnline');
    return t('event.instanceOffline');
  };

  const localChannelScopeText = (channel: NotificationChannel) => {
    if (channel.service_scopes.length === 0) return t('notifications.allServices');
    if (channel.service_scopes.length === 1) {
      const [scope] = channel.service_scopes;
      return `${scope.environment}/${scope.name}`;
    }
    return t('notifications.servicesCount', { count: channel.service_scopes.length });
  };

  async function loadNotifications(silent = false) {
    setIsLoading(true);
    setError(null);
    try {
      const [nextChannels, nextServices] = await Promise.all([
        listNotificationChannels(),
        listNotificationServices(),
      ]);
      setChannels(nextChannels);
      setServices(nextServices);
      if (!silent) {
        onNotify(t('notifications.refreshed'), 'success');
      }
    } catch (loadError) {
      if (isAuthRequiredError(loadError)) {
        onAuthRequired();
        return;
      }
      const message = getApiErrorMessage(loadError);
      setError(message);
      if (!silent) {
        onNotify(t('notifications.failed', { message }), 'warn');
      }
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadNotifications(true);
  }, []);

  function updateEventType(eventType: NotificationEventType, checked: boolean) {
    setForm((current) => ({
      ...current,
      eventTypes: checked
        ? [...current.eventTypes, eventType]
        : current.eventTypes.filter((item) => item !== eventType),
    }));
  }

  function updateServiceScope(key: string, checked: boolean) {
    setForm((current) => ({
      ...current,
      serviceScopeKeys: checked
        ? [...current.serviceScopeKeys, key]
        : current.serviceScopeKeys.filter((item) => item !== key),
    }));
  }

  async function saveChannel(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setIsSaving(true);

    const webhookUrl = form.webhookUrl.trim();
    const graceSeconds = includesMissedStart ? Number(form.missedStartGraceSeconds) || 300 : 300;
    const customConfig =
      form.provider === 'custom'
        ? {
            method: form.customMethod,
            query_params: normalizeParams(form.queryParams),
            body_params: form.customMethod === 'POST' ? normalizeParams(form.bodyParams) : [],
          }
        : undefined;
    const payload = {
      name: form.name.trim(),
      provider: form.provider,
      service_scopes: form.serviceScopeKeys.map(scopeFromKey),
      event_types: form.eventTypes,
      missed_start_grace_seconds: graceSeconds,
      ...(customConfig ? { custom_config: customConfig } : {}),
      ...(form.provider !== 'custom' && form.originalProvider === 'custom'
        ? { custom_config: null }
        : {}),
    };

    try {
      if (form.id) {
        await updateNotificationChannel(
          form.id,
          webhookUrl ? { ...payload, webhook_url: webhookUrl } : payload,
        );
        onNotify(t('notifications.updated', { name: payload.name }), 'success');
      } else {
        const createPayload: NotificationChannelInput = {
          ...payload,
          webhook_url: webhookUrl,
        };
        await createNotificationChannel(createPayload);
        onNotify(t('notifications.created', { name: payload.name }), 'success');
      }
      setForm(EMPTY_FORM);
      await loadNotifications(true);
    } catch (saveError) {
      if (isAuthRequiredError(saveError)) {
        onAuthRequired();
        return;
      }
      const message = getApiErrorMessage(saveError);
      setError(message);
      onNotify(t('notifications.saveFailed', { message }), 'warn');
    } finally {
      setIsSaving(false);
    }
  }

  async function runTest(channel: NotificationChannel) {
    setError(null);
    setTestingChannelId(channel.id);
    try {
      const response = await testNotificationChannel(channel.id);
      onNotify(t('notifications.testAccepted', { provider: providerLabel(response.provider, t), name: channel.name }), 'success');
    } catch (testError) {
      if (isAuthRequiredError(testError)) {
        onAuthRequired();
        return;
      }
      const message = getApiErrorMessage(testError);
      setError(message);
      onNotify(t('notifications.testFailed', { message }), 'warn');
    } finally {
      setTestingChannelId((current) => (current === channel.id ? null : current));
    }
  }

  async function toggleChannelEnabled(channel: NotificationChannel) {
    setError(null);
    setEnabledToggleId(channel.id);
    try {
      const updated = await setNotificationChannelEnabled(channel.id, !channel.enabled);
      setChannels((current) =>
        current.map((item) => (item.id === updated.id ? updated : item)),
      );
      onNotify(
        t(updated.enabled ? 'notifications.enabledUpdated' : 'notifications.disabledUpdated', {
          name: updated.name,
        }),
        'success',
      );
    } catch (toggleError) {
      if (isAuthRequiredError(toggleError)) {
        onAuthRequired();
        return;
      }
      const message = getApiErrorMessage(toggleError);
      setError(message);
      onNotify(t('notifications.toggleFailed', { message }), 'warn');
    } finally {
      setEnabledToggleId((current) => (current === channel.id ? null : current));
    }
  }

  async function removeChannel(channel: NotificationChannel) {
    if (!window.confirm(t('notifications.deleteConfirm', { name: channel.name }))) return;
    setError(null);
    setDeletingChannelId(channel.id);
    try {
      await deleteNotificationChannel(channel.id);
      onNotify(t('notifications.deleted', { name: channel.name }), 'success');
      if (form.id === channel.id) {
        setForm(EMPTY_FORM);
      }
      await loadNotifications(true);
    } catch (deleteError) {
      if (isAuthRequiredError(deleteError)) {
        onAuthRequired();
        return;
      }
      const message = getApiErrorMessage(deleteError);
      setError(message);
      onNotify(t('notifications.deleteFailed', { message }), 'warn');
    } finally {
      setDeletingChannelId((current) => (current === channel.id ? null : current));
    }
  }

  function insertVariable(
    params: NotificationCustomParam[],
    index: number,
    variableName: string,
  ) {
    const token = `{{ ${variableName} }}`;
    return params.map((param, itemIndex) =>
      itemIndex === index
        ? {
            key: param.key.trim() ? param.key : variableName,
            value: param.value ? `${param.value} ${token}` : token,
          }
        : param,
    );
  }

  function renderParamEditor(
    paramGroup: 'query' | 'body',
    title: string,
    params: NotificationCustomParam[],
    setParams: (params: NotificationCustomParam[]) => void,
    addLabel: string,
    keyLabelKey: MessageKey,
    valueLabelKey: MessageKey,
  ) {
    return (
      <div>
        <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">{title}</div>
        <div className="mt-2 space-y-2">
          {params.map((param, index) => {
            const pickerId = `${paramGroup}-${index}`;
            return (
              <div className="rounded-lg border border-slate-200 bg-white p-2" key={index}>
                <div className="grid grid-cols-[minmax(0,1fr)_36px] gap-2">
                  <div className="grid min-w-0 grid-cols-1 gap-2 sm:grid-cols-[minmax(96px,0.42fr)_minmax(0,1fr)]">
                    <input
                      aria-label={t(keyLabelKey, { index: index + 1 })}
                      className="min-w-0 rounded-lg border border-slate-200 px-2.5 py-2 text-xs font-semibold outline-hidden transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                      onChange={(event) => {
                        const next = [...params];
                        next[index] = { ...next[index], key: event.target.value };
                        setParams(next);
                      }}
                      value={param.key}
                    />
                    <div className="relative min-w-0">
                      <input
                        aria-label={t(valueLabelKey, { index: index + 1 })}
                        className="w-full min-w-0 rounded-lg border border-slate-200 px-2.5 py-2 pr-10 font-mono text-xs font-semibold outline-hidden transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                        onChange={(event) => {
                          const next = [...params];
                          next[index] = { ...next[index], value: event.target.value };
                          setParams(next);
                        }}
                        title={param.value}
                        value={param.value}
                      />
                      <button
                        aria-expanded={openVariablePicker === pickerId}
                        aria-label={t('notifications.insertField')}
                        className="ui-pressable absolute right-1 top-1 grid h-7 w-7 place-items-center rounded-md text-slate-400 hover:bg-indigo-50 hover:text-indigo-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-100"
                        onClick={() =>
                          setOpenVariablePicker((current) =>
                            current === pickerId ? null : pickerId,
                          )
                        }
                        title={t('notifications.insertField')}
                        type="button"
                      >
                        <Braces className="h-4 w-4" />
                      </button>
                      {openVariablePicker === pickerId ? (
                        <div className="ui-popover-enter absolute right-0 top-10 z-20 max-h-64 w-full min-w-56 overflow-y-auto rounded-lg border border-slate-200 bg-white p-1 shadow-lg">
                          {CUSTOM_VARIABLES.map((variableName) => (
                            <button
                              className="ui-pressable block w-full rounded-md px-2.5 py-2 text-left font-mono text-xs font-semibold text-slate-700 hover:bg-indigo-50 hover:text-indigo-700"
                              key={variableName}
                              onClick={() => {
                                setParams(insertVariable(params, index, variableName));
                                setOpenVariablePicker(null);
                              }}
                              type="button"
                            >
                              {variableName}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                  <button
                    aria-label={t('notifications.deleteParam', { index: index + 1 })}
                    className="ui-pressable grid h-9 w-9 place-items-center rounded-md text-slate-400 hover:bg-rose-50 hover:text-rose-600"
                    onClick={() => {
                      setParams(params.filter((_, itemIndex) => itemIndex !== index));
                      setOpenVariablePicker(null);
                    }}
                    title={t('notifications.deleteParam', { index: index + 1 })}
                    type="button"
                  >
                    <Trash2 className="h-4 w-4" />
                  </button>
                </div>
              </div>
            );
          })}
          <button
            className="ui-pressable flex w-full items-center justify-center gap-2 rounded-lg border border-dashed border-slate-300 px-3 py-2 text-xs font-bold text-slate-600 hover:border-indigo-200 hover:bg-indigo-50 hover:text-indigo-700"
            onClick={() => setParams([...params, { key: '', value: '' }])}
            type="button"
          >
            <Plus className="h-4 w-4" />
            {addLabel}
          </button>
        </div>
      </div>
    );
  }

  const canSubmit =
    form.name.trim().length > 0 &&
    (isEditing || form.webhookUrl.trim().length > 0) &&
    form.eventTypes.length > 0 &&
    !isSaving;

  return (
    <div className="ui-page-enter mx-auto max-w-7xl space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <div className="mb-2 inline-flex items-center gap-2 rounded-full border border-indigo-100 bg-indigo-50 px-2.5 py-1 text-[11px] font-bold uppercase tracking-wide text-indigo-700">
            <Bell className="h-3.5 w-3.5" />
            {t('notifications.badge')}
          </div>
          <h2 className="text-3xl font-bold tracking-tight text-slate-900">{t('notifications.title')}</h2>
          <p className="text-sm font-medium text-slate-500">
            {t('notifications.subtitle')}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            aria-busy={isLoading}
            className="ui-pressable flex min-w-[104px] items-center justify-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 shadow-xs hover:bg-slate-50 disabled:cursor-wait disabled:opacity-50"
            disabled={isLoading}
            onClick={() => void loadNotifications()}
            type="button"
          >
            <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            <span>{isLoading ? t('button.refreshing') : t('button.refresh')}</span>
          </button>
          <button
            className="ui-pressable flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-bold text-white shadow-xs hover:bg-indigo-800"
            onClick={() => setForm(EMPTY_FORM)}
          >
            <Plus className="h-4 w-4" />
            <span>{t('notifications.newChannel')}</span>
          </button>
        </div>
      </div>

      {error ? (
        <div
          className="ui-panel-state-enter flex items-start gap-3 rounded-lg border border-rose-200 bg-rose-50 p-4 text-sm font-semibold text-rose-900"
          role="alert"
        >
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_380px]">
        <section className="overflow-hidden rounded-xl border border-slate-200 bg-white shadow-xs">
          <div className="flex items-center justify-between border-b border-slate-100 bg-slate-50 px-4 py-3">
            <h3 className="text-sm font-bold text-slate-800">{t('notifications.channels')}</h3>
            <span className="text-xs font-semibold text-slate-500">{t('notifications.configured', { count: channels.length })}</span>
          </div>

          {channels.length > 0 ? (
            <div>
              <div className="hidden border-b border-slate-100 bg-white px-4 py-2.5 text-[11px] font-bold uppercase tracking-wide text-slate-400 lg:grid lg:grid-cols-[minmax(220px,1.05fr)_minmax(270px,1.55fr)_104px] lg:gap-3">
                <span>{t('notifications.channel')}</span>
                <span>
                  {t('notifications.scope')} / {t('notifications.events')}
                </span>
                <span className="text-right">{t('notifications.actions')}</span>
              </div>

              <div className="divide-y divide-slate-100">
                {channels.map((channel) => (
                  <article
                    key={channel.id}
                    className={`grid gap-4 px-4 py-4 transition-colors lg:grid-cols-[minmax(220px,1.05fr)_minmax(270px,1.55fr)_104px] lg:items-center lg:gap-3 ${
                      form.id === channel.id ? 'bg-indigo-50/35' : 'hover:bg-slate-50/70'
                    }`}
                  >
                    <div className="min-w-0">
                      <div className="flex min-w-0 flex-wrap items-center gap-2">
                        <div className="min-w-0 truncate text-sm font-bold text-slate-950">{channel.name}</div>
                        <span className="inline-flex shrink-0 items-center whitespace-nowrap rounded-md border border-indigo-100 bg-indigo-50 px-2 py-0.5 text-[11px] font-bold text-indigo-700">
                          {providerLabel(channel.provider, t)}
                        </span>
                        <button
                          aria-label={t(channel.enabled ? 'notifications.disableTitle' : 'notifications.enableTitle', {
                            name: channel.name,
                          })}
                          aria-pressed={channel.enabled}
                          aria-busy={enabledToggleId === channel.id}
                          className={`ui-pressable inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-md border px-2 py-0.5 text-[11px] font-bold disabled:cursor-wait disabled:opacity-70 ${
                            channel.enabled
                              ? 'border-emerald-200 bg-emerald-50 text-emerald-700 hover:bg-emerald-100'
                              : 'border-slate-200 bg-slate-50 text-slate-500 hover:bg-slate-100'
                          }`}
                          disabled={enabledToggleId === channel.id}
                          onClick={() => void toggleChannelEnabled(channel)}
                          title={t(channel.enabled ? 'notifications.disableTitle' : 'notifications.enableTitle', {
                            name: channel.name,
                          })}
                          type="button"
                        >
                          <span
                            className={`h-1.5 w-1.5 rounded-full ${
                              channel.enabled ? 'bg-emerald-500' : 'bg-slate-400'
                            }`}
                          />
                          {channel.enabled ? t('notifications.enabled') : t('notifications.disabled')}
                        </button>
                      </div>
                      <div className="mt-2 flex min-w-0 items-center gap-2 rounded-md bg-slate-50 px-2.5 py-1.5 ring-1 ring-slate-100">
                        <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-slate-300" />
                        <span className="min-w-0 truncate font-mono text-[11px] font-semibold text-slate-500">
                          {channel.webhook_url_masked}
                        </span>
                      </div>
                    </div>

                    <div className="grid min-w-0 gap-3 sm:grid-cols-[minmax(140px,0.75fr)_minmax(0,1fr)] sm:items-start">
                      <div className="min-w-0">
                        <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-slate-400 lg:hidden">
                          {t('notifications.scope')}
                        </div>
                        <span className="inline-flex max-w-full rounded-md border border-slate-200 bg-white px-2.5 py-1 text-xs font-bold text-slate-700">
                          <span className="truncate">{localChannelScopeText(channel)}</span>
                        </span>
                      </div>

                      <div className="min-w-0">
                        <div className="mb-1 text-[10px] font-bold uppercase tracking-wide text-slate-400 lg:hidden">
                          {t('notifications.events')}
                        </div>
                        <div className="flex min-w-0 flex-wrap gap-1.5">
                          {channel.event_types.slice(0, 3).map((eventType) => (
                            <span
                              key={eventType}
                              className="whitespace-nowrap rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-bold text-slate-600"
                            >
                              {localEventLabel(eventType)}
                            </span>
                          ))}
                          {channel.event_types.length > 3 ? (
                            <span className="whitespace-nowrap rounded-md border border-slate-200 bg-white px-2 py-0.5 text-[11px] font-bold text-slate-500">
                              +{channel.event_types.length - 3}
                            </span>
                          ) : null}
                        </div>
                      </div>
                    </div>

                    <div className="flex w-fit items-center gap-1 rounded-lg border border-slate-200 bg-white p-1 shadow-xs lg:justify-self-end">
                      <button
                        aria-label={t('notifications.testTitle', { name: channel.name })}
                        aria-busy={testingChannelId === channel.id}
                        className="ui-pressable grid h-8 w-8 place-items-center rounded-md text-slate-500 hover:bg-indigo-50 hover:text-indigo-600 disabled:cursor-wait disabled:opacity-60"
                        disabled={testingChannelId === channel.id}
                        onClick={() => void runTest(channel)}
                        title={t('notifications.testTitle', { name: channel.name })}
                        type="button"
                      >
                        {testingChannelId === channel.id ? (
                          <RefreshCw className="h-4 w-4 animate-spin" />
                        ) : (
                          <Send className="h-4 w-4" />
                        )}
                      </button>
                      <button
                        aria-label={t('notifications.editTitle', { name: channel.name })}
                        className="ui-pressable grid h-8 w-8 place-items-center rounded-md text-slate-500 hover:bg-indigo-50 hover:text-indigo-600"
                        onClick={() => setForm(channelToForm(channel))}
                        title={t('notifications.editTitle', { name: channel.name })}
                        type="button"
                      >
                        <Edit3 className="h-4 w-4" />
                      </button>
                      <button
                        aria-label={t('notifications.deleteTitle', { name: channel.name })}
                        aria-busy={deletingChannelId === channel.id}
                        className="ui-pressable grid h-8 w-8 place-items-center rounded-md text-slate-500 hover:bg-rose-50 hover:text-rose-600 disabled:cursor-wait disabled:opacity-60"
                        disabled={deletingChannelId === channel.id}
                        onClick={() => void removeChannel(channel)}
                        title={t('notifications.deleteTitle', { name: channel.name })}
                        type="button"
                      >
                        {deletingChannelId === channel.id ? (
                          <RefreshCw className="h-4 w-4 animate-spin" />
                        ) : (
                          <Trash2 className="h-4 w-4" />
                        )}
                      </button>
                    </div>
                  </article>
                ))}
              </div>
            </div>
          ) : (
            <div className="flex min-h-[280px] flex-col items-center justify-center gap-3 p-8 text-center">
              <div className="flex h-12 w-12 items-center justify-center rounded-xl bg-indigo-50 text-indigo-600">
                <Bell className="h-6 w-6" />
              </div>
              <div>
                <h3 className="text-sm font-bold text-slate-900">{t('notifications.noChannels')}</h3>
                <p className="mt-1 text-sm font-medium text-slate-500">
                  {t('notifications.noChannelsDescription')}
                </p>
              </div>
            </div>
          )}
        </section>

        <aside className="rounded-xl border border-slate-200 bg-white p-5 shadow-xs">
          <form className="space-y-5" onSubmit={(event) => void saveChannel(event)}>
            <div className="flex items-start justify-between gap-3">
              <div>
                <h3 className="text-sm font-bold text-slate-900">
                  {isEditing ? t('notifications.editChannel') : t('notifications.newChannel')}
                </h3>
                <p className="mt-1 text-xs font-medium text-slate-500">
                  {isEditing ? t('notifications.editHelp') : t('notifications.newHelp')}
                </p>
              </div>
              {isEditing ? (
                <button
                  className="ui-pressable rounded-lg p-1.5 text-slate-400 hover:bg-slate-50 hover:text-slate-700"
                  onClick={() => setForm(EMPTY_FORM)}
                  title={t('notifications.clearFormTitle')}
                  type="button"
                >
                  <X className="h-4 w-4" />
                </button>
              ) : null}
            </div>

            <label className="block">
              <span className="text-[11px] font-bold uppercase tracking-wide text-slate-500">{t('notifications.name')}</span>
              <input
                className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold outline-hidden transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
                required
                value={form.name}
              />
            </label>

            <div>
              <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">
                {t('notifications.provider')}
              </div>
              <div className="mt-2 grid grid-cols-1 gap-2 rounded-lg border border-slate-200 bg-slate-50 p-1 sm:grid-cols-3">
                {PROVIDER_VALUES.map((providerValue) => {
                  const active = form.provider === providerValue;
                  return (
                    <button
                      aria-pressed={active}
                      className={`ui-pressable rounded-md px-3 py-2 text-sm font-bold ${
                        active
                          ? 'bg-white text-indigo-700 shadow-xs ring-1 ring-indigo-100'
                          : 'text-slate-500 hover:bg-white/70 hover:text-slate-800'
                      }`}
                      key={providerValue}
                      onClick={() =>
                        setForm((current) => ({ ...current, provider: providerValue }))
                      }
                      type="button"
                    >
                      {t(providerLabelKey(providerValue))}
                    </button>
                  );
                })}
              </div>
            </div>

            <label className="block">
              <span className="text-[11px] font-bold uppercase tracking-wide text-slate-500">{t('notifications.webhookUrl')}</span>
              <input
                className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold outline-hidden transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                onChange={(event) => setForm((current) => ({ ...current, webhookUrl: event.target.value }))}
                placeholder={isEditing ? t('notifications.unchanged') : 'https://...'}
                required={!isEditing}
                type="url"
                value={form.webhookUrl}
              />
            </label>

            {form.provider === 'custom' ? (
              <div className="space-y-4 rounded-lg border border-slate-200 bg-slate-50/70 p-3">
                <div>
                  <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">
                    {t('notifications.method')}
                  </div>
                  <div className="mt-2 grid grid-cols-1 gap-2 rounded-lg border border-slate-200 bg-white p-1 sm:grid-cols-2">
                    {WEBHOOK_METHOD_VALUES.map((methodValue) => {
                      const active = form.customMethod === methodValue;
                      return (
                        <button
                          aria-pressed={active}
                          className={`ui-pressable rounded-md px-3 py-2 text-xs font-bold ${
                            active
                              ? 'bg-indigo-50 text-indigo-700 shadow-xs ring-1 ring-indigo-100'
                              : 'text-slate-500 hover:bg-slate-50 hover:text-slate-800'
                          }`}
                          key={methodValue}
                          onClick={() =>
                            setForm((current) => ({ ...current, customMethod: methodValue }))
                          }
                          type="button"
                        >
                          {methodValue}
                        </button>
                      );
                    })}
                  </div>
                </div>

                {renderParamEditor(
                  'query',
                  t('notifications.queryParams'),
                  form.queryParams,
                  (queryParams) => setForm((current) => ({ ...current, queryParams })),
                  t('notifications.addQueryParam'),
                  'notifications.queryParamKey',
                  'notifications.queryParamValue',
                )}

                {form.customMethod === 'POST'
                  ? renderParamEditor(
                      'body',
                      t('notifications.bodyParams'),
                      form.bodyParams,
                      (bodyParams) => setForm((current) => ({ ...current, bodyParams })),
                      t('notifications.addBodyParam'),
                      'notifications.bodyParamKey',
                      'notifications.bodyParamValue',
                    )
                  : null}
              </div>
            ) : null}

            <div>
              <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">{t('notifications.events')}</div>
              <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-1">
                {EVENTS.map((eventType) => {
                  const checked = form.eventTypes.includes(eventType.value);
                  return (
                    <label
                      className={`flex min-w-0 items-center gap-2 rounded-lg border px-3 py-2 text-xs font-bold transition-colors ${
                        checked
                          ? 'border-indigo-100 bg-indigo-50/60 text-slate-900'
                          : 'border-slate-200 bg-white text-slate-600 hover:border-slate-300 hover:bg-slate-50'
                      }`}
                      key={eventType.value}
                    >
                      <StyledCheckbox
                        checked={checked}
                        onChange={(event) => updateEventType(eventType.value, event.target.checked)}
                      />
                      <span className="truncate">{localEventLabel(eventType.value)}</span>
                    </label>
                  );
                })}
              </div>
            </div>

            {includesMissedStart ? (
              <label className="block">
                <span className="text-[11px] font-bold uppercase tracking-wide text-slate-500">
                  {t('notifications.missedStartGraceSeconds')}
                </span>
                <input
                  className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm font-semibold outline-hidden transition focus:border-indigo-500 focus:ring-2 focus:ring-indigo-100"
                  max={86400}
                  min={1}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, missedStartGraceSeconds: event.target.value }))
                  }
                  type="number"
                  value={form.missedStartGraceSeconds}
                />
              </label>
            ) : null}

            <div>
              <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">{t('nav.services')}</div>
              <div className="mt-2 max-h-52 space-y-2 overflow-y-auto rounded-lg border border-slate-200 p-2">
                <label
                  className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-xs font-bold transition-colors ${
                    allServicesSelected
                      ? 'bg-indigo-50 text-slate-900'
                      : 'text-slate-600 hover:bg-slate-50 hover:text-slate-800'
                  }`}
                >
                  <StyledCheckbox
                    checked={allServicesSelected}
                    onChange={() => setForm((current) => ({ ...current, serviceScopeKeys: [] }))}
                  />
                  <span>{t('notifications.allServices')}</span>
                </label>
                {serviceOptions.map((service) => {
                  const key = scopeKey(service);
                  const checked = selectedServiceKeys.has(key);
                  return (
                    <label
                      className={`flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-xs font-bold transition-colors ${
                        checked
                          ? 'bg-indigo-50 text-slate-900'
                          : 'text-slate-600 hover:bg-slate-50 hover:text-slate-800'
                      }`}
                      key={key}
                    >
                      <StyledCheckbox
                        checked={checked}
                        onChange={(event) => updateServiceScope(key, event.target.checked)}
                      />
                      <span className="truncate">
                        {service.environment}/{service.name}
                      </span>
                    </label>
                  );
                })}
              </div>
            </div>

            <button
              aria-busy={isSaving}
              className="ui-pressable flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-bold text-white hover:bg-indigo-800 disabled:cursor-not-allowed disabled:opacity-50"
              disabled={!canSubmit}
              type="submit"
            >
              {isSaving ? (
                <RefreshCw className="h-4 w-4 animate-spin" />
              ) : (
                <CheckCircle2 className="h-4 w-4" />
              )}
              <span>{isEditing ? t('notifications.saveChanges') : t('notifications.createChannel')}</span>
            </button>
          </form>
        </aside>
      </div>
    </div>
  );
}

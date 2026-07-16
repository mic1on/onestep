import { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  AlertTriangle,
  Bell,
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
  testNotificationChannel,
  updateNotificationChannel,
  type NotificationChannel,
  type NotificationChannelInput,
  type NotificationEventType,
  type NotificationProvider,
  type NotificationServiceScope,
} from '../api';
import { useI18n } from '../i18n';

interface NotificationSettingsPageProps {
  onAuthRequired: () => void;
  onNotify: (message: string, type?: 'success' | 'info' | 'warn') => void;
}

type FormState = {
  id: string | null;
  name: string;
  provider: NotificationProvider;
  webhookUrl: string;
  enabled: boolean;
  serviceScopeKeys: string[];
  eventTypes: NotificationEventType[];
  missedStartGraceSeconds: string;
};

const PROVIDERS: Array<{ value: NotificationProvider; label: string }> = [
  { value: 'feishu', label: 'Feishu' },
  { value: 'wechat_work', label: 'WeCom' },
];

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
  webhookUrl: '',
  enabled: true,
  serviceScopeKeys: [],
  eventTypes: DEFAULT_EVENTS,
  missedStartGraceSeconds: '300',
};

function providerLabel(provider: NotificationProvider) {
  return PROVIDERS.find((item) => item.value === provider)?.label ?? provider;
}

function eventLabel(eventType: NotificationEventType) {
  return EVENTS.find((item) => item.value === eventType)?.label ?? eventType;
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

function channelToForm(channel: NotificationChannel): FormState {
  return {
    id: channel.id,
    name: channel.name,
    provider: channel.provider,
    webhookUrl: '',
    enabled: channel.enabled,
    serviceScopeKeys: channel.service_scopes.map(scopeKey),
    eventTypes: channel.event_types,
    missedStartGraceSeconds: String(channel.missed_start_grace_seconds),
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

function formatDate(value: string) {
  return new Date(value).toLocaleString();
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
  const [error, setError] = useState<string | null>(null);

  const isEditing = form.id !== null;
  const includesMissedStart = form.eventTypes.includes('task_missed_start');
  const selectedServiceKeys = new Set(form.serviceScopeKeys);
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
    const payload = {
      name: form.name.trim(),
      provider: form.provider,
      enabled: form.enabled,
      service_scopes: form.serviceScopeKeys.map(scopeFromKey),
      event_types: form.eventTypes,
      missed_start_grace_seconds: graceSeconds,
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
    try {
      const response = await testNotificationChannel(channel.id);
      onNotify(t('notifications.testAccepted', { provider: providerLabel(response.provider), name: channel.name }), 'success');
    } catch (testError) {
      if (isAuthRequiredError(testError)) {
        onAuthRequired();
        return;
      }
      const message = getApiErrorMessage(testError);
      setError(message);
      onNotify(t('notifications.testFailed', { message }), 'warn');
    }
  }

  async function removeChannel(channel: NotificationChannel) {
    if (!window.confirm(t('notifications.deleteConfirm', { name: channel.name }))) return;
    setError(null);
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
    }
  }

  const canSubmit =
    form.name.trim().length > 0 &&
    (isEditing || form.webhookUrl.trim().length > 0) &&
    form.eventTypes.length > 0 &&
    !isSaving;

  return (
    <div className="max-w-7xl mx-auto space-y-6 animate-fadeIn">
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
            className="flex items-center gap-1.5 rounded-lg border border-slate-200 bg-white px-3 py-2 text-xs font-bold text-slate-700 shadow-xs transition-colors hover:bg-slate-50 disabled:opacity-50"
            disabled={isLoading}
            onClick={() => void loadNotifications()}
          >
            <RefreshCw className={`h-4 w-4 ${isLoading ? 'animate-spin' : ''}`} />
            <span>{isLoading ? t('button.refreshing') : t('button.refresh')}</span>
          </button>
          <button
            className="flex items-center gap-1.5 rounded-lg bg-indigo-600 px-3 py-2 text-xs font-bold text-white shadow-xs transition-colors hover:bg-indigo-800"
            onClick={() => setForm(EMPTY_FORM)}
          >
            <Plus className="h-4 w-4" />
            <span>{t('notifications.newChannel')}</span>
          </button>
        </div>
      </div>

      {error ? (
        <div className="flex items-start gap-3 rounded-xl border border-rose-200 bg-rose-50 p-4 text-sm font-semibold text-rose-900">
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
            <div className="overflow-x-auto">
              <table className="w-full min-w-[900px] border-collapse text-left text-sm">
                <thead>
                  <tr className="border-b border-slate-100 bg-white text-[11px] uppercase tracking-wide text-slate-400">
                    <th className="px-4 py-3 font-bold">{t('notifications.channel')}</th>
                    <th className="px-4 py-3 font-bold">{t('notifications.provider')}</th>
                    <th className="px-4 py-3 font-bold">{t('notifications.status')}</th>
                    <th className="px-4 py-3 font-bold">{t('notifications.scope')}</th>
                    <th className="px-4 py-3 font-bold">{t('notifications.events')}</th>
                    <th className="px-4 py-3 font-bold">{t('notifications.updatedAt')}</th>
                    <th className="px-4 py-3 text-right font-bold">{t('notifications.actions')}</th>
                  </tr>
                </thead>
                <tbody>
                  {channels.map((channel) => (
                    <tr key={channel.id} className="border-b border-slate-100 last:border-0">
                      <td className="px-4 py-3">
                        <div className="font-bold text-slate-900">{channel.name}</div>
                        <div className="mt-1 max-w-xs truncate font-mono text-[11px] font-medium text-slate-400">
                          {channel.webhook_url_masked}
                        </div>
                      </td>
                      <td className="px-4 py-3 font-semibold text-slate-700">
                        {providerLabel(channel.provider)}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex items-center gap-1.5 rounded-full border px-2 py-1 text-[11px] font-bold ${
                            channel.enabled
                              ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                              : 'border-slate-200 bg-slate-50 text-slate-500'
                          }`}
                        >
                          <span
                            className={`h-1.5 w-1.5 rounded-full ${
                              channel.enabled ? 'bg-emerald-500' : 'bg-slate-400'
                            }`}
                          />
                          {channel.enabled ? t('notifications.enabled') : t('notifications.disabled')}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs font-semibold text-slate-600">
                        {localChannelScopeText(channel)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex max-w-xs flex-wrap gap-1.5">
                          {channel.event_types.slice(0, 3).map((eventType) => (
                            <span
                              key={eventType}
                              className="rounded-full border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11px] font-bold text-slate-600"
                            >
                              {localEventLabel(eventType)}
                            </span>
                          ))}
                          {channel.event_types.length > 3 ? (
                            <span className="rounded-full border border-slate-200 px-2 py-0.5 text-[11px] font-bold text-slate-500">
                              +{channel.event_types.length - 3}
                            </span>
                          ) : null}
                        </div>
                      </td>
                      <td className="px-4 py-3 text-xs font-semibold text-slate-500">
                        {formatDate(channel.updated_at)}
                      </td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-1.5">
                          <button
                            className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-50 hover:text-indigo-600"
                            onClick={() => void runTest(channel)}
                            title={t('notifications.testTitle', { name: channel.name })}
                          >
                            <Send className="h-4 w-4" />
                          </button>
                          <button
                            className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-slate-50 hover:text-indigo-600"
                            onClick={() => setForm(channelToForm(channel))}
                            title={t('notifications.editTitle', { name: channel.name })}
                          >
                            <Edit3 className="h-4 w-4" />
                          </button>
                          <button
                            className="rounded-lg p-2 text-slate-500 transition-colors hover:bg-rose-50 hover:text-rose-600"
                            onClick={() => void removeChannel(channel)}
                            title={t('notifications.deleteTitle', { name: channel.name })}
                          >
                            <Trash2 className="h-4 w-4" />
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
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
                  className="rounded-lg p-1.5 text-slate-400 transition-colors hover:bg-slate-50 hover:text-slate-700"
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
              <div className="mt-2 grid grid-cols-2 gap-2 rounded-lg border border-slate-200 bg-slate-50 p-1">
                {PROVIDERS.map((provider) => {
                  const active = form.provider === provider.value;
                  return (
                    <button
                      aria-pressed={active}
                      className={`rounded-md px-3 py-2 text-sm font-bold transition-all ${
                        active
                          ? 'bg-white text-indigo-700 shadow-xs ring-1 ring-indigo-100'
                          : 'text-slate-500 hover:bg-white/70 hover:text-slate-800'
                      }`}
                      key={provider.value}
                      onClick={() =>
                        setForm((current) => ({ ...current, provider: provider.value }))
                      }
                      type="button"
                    >
                      {provider.label}
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

            <label className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-sm font-bold text-slate-700">
              <input
                checked={form.enabled}
                className="h-4 w-4 rounded border-slate-300 text-indigo-600"
                onChange={(event) => setForm((current) => ({ ...current, enabled: event.target.checked }))}
                type="checkbox"
              />
              <span>{form.enabled ? t('notifications.enabled') : t('notifications.disabled')}</span>
            </label>

            <div>
              <div className="text-[11px] font-bold uppercase tracking-wide text-slate-500">{t('notifications.events')}</div>
              <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-1">
                {EVENTS.map((eventType) => (
                  <label
                    className="flex min-w-0 items-center gap-2 rounded-lg border border-slate-200 px-3 py-2 text-xs font-bold text-slate-700"
                    key={eventType.value}
                  >
                    <input
                      checked={form.eventTypes.includes(eventType.value)}
                      className="h-4 w-4 rounded border-slate-300 text-indigo-600"
                      onChange={(event) => updateEventType(eventType.value, event.target.checked)}
                      type="checkbox"
                    />
                    <span className="truncate">{localEventLabel(eventType.value)}</span>
                  </label>
                ))}
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
                <label className="flex items-center gap-2 rounded-md px-2 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50">
                  <input
                    checked={form.serviceScopeKeys.length === 0}
                    className="h-4 w-4 rounded border-slate-300 text-indigo-600"
                    onChange={() => setForm((current) => ({ ...current, serviceScopeKeys: [] }))}
                    type="checkbox"
                  />
                  <span>{t('notifications.allServices')}</span>
                </label>
                {serviceOptions.map((service) => {
                  const key = scopeKey(service);
                  return (
                    <label
                      className="flex min-w-0 items-center gap-2 rounded-md px-2 py-1.5 text-xs font-bold text-slate-700 hover:bg-slate-50"
                      key={key}
                    >
                      <input
                        checked={selectedServiceKeys.has(key)}
                        className="h-4 w-4 rounded border-slate-300 text-indigo-600"
                        onChange={(event) => updateServiceScope(key, event.target.checked)}
                        type="checkbox"
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
              className="flex w-full items-center justify-center gap-2 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-bold text-white transition-colors hover:bg-indigo-800 disabled:cursor-not-allowed disabled:opacity-50"
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

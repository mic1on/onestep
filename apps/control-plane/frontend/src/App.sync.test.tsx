import '@testing-library/jest-dom/vitest';
import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { getResourceCatalog, loadControlPlaneData, type ServiceCommandFanoutResponse } from './api';
import { I18nProvider, type Locale } from './i18n';
import type { Service } from './types';

// Keep the command client real so these tests cover the request sent by the UI.
vi.mock('./api', async (importOriginal) => ({
  ...await importOriginal<typeof import('./api')>(),
  getResourceCatalog: vi.fn(),
  loadControlPlaneData: vi.fn(),
}));

const service: Service = {
  id: 'billing-sync:prod', apiName: 'billing-sync', environment: 'prod',
  name: 'billing-sync / prod', description: null, viewStatus: 'running',
  uptimeReferenceAt: null, throughputPerMin: 0, successRate: 100, errorCount: 0,
  totalInstances: 1, activeInstances: 1, standbyInstances: 0,
  totalTaskCount: 0, failingTaskCount: 0, onlineTaskCount: 0,
};

const accepted: ServiceCommandFanoutResponse = {
  kind: 'sync_now', target_mode: 'all_online', offline_behavior: 'skip',
  noop_reason_code: null, noop_reason_message: null,
  counts: { dispatched: 1, queued: 0, skipped: 0, rejected: 0, total: 1 },
  dispatched: [{
    instance_id: 'instance-1', node_name: null, connectivity: 'online',
    session_id: 'session-1', command_id: 'command-1', outcome: 'dispatched',
    reason_code: null, reason_message: null,
  }],
  queued: [], skipped: [], rejected: [],
};

function mockService(activeInstances = 1) {
  vi.mocked(getResourceCatalog).mockResolvedValue({ resources: [] });
  vi.mocked(loadControlPlaneData).mockResolvedValue({
    services: [{ ...service, activeInstances }],
    tasks: [], instances: [], logs: [], selectedServiceId: service.id,
    sourceKindCounts: {},
    serviceSummary: {
      total_services: 1, online_services: 1, attention_services: 0,
      offline_services: 0, ready_services: 1, total_instances: 1,
      online_instances: activeInstances, total_tasks: 0, failing_tasks: 0,
    },
  });
}

function renderApp(locale: Locale = 'en') {
  window.history.replaceState(null, '', '/services/billing-sync%3Aprod');
  render(<I18nProvider initialLocale={locale}><App /></I18nProvider>);
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status, headers: { 'Content-Type': 'application/json' },
  });
}

describe('service sync action', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    window.localStorage.clear();
    mockService();
  });

  afterEach(() => {
    cleanup();
    vi.restoreAllMocks();
  });

  it.each([
    { locale: 'en' as const, button: 'Sync Now', pending: 'Requesting Sync...', message: 'Sync command accepted for billing-sync / prod.' },
    { locale: 'zh-CN' as const, button: '立即同步', pending: '下发同步中...', message: 'billing-sync / prod 的同步命令已接受。' },
  ])('requests sync and shows only command acceptance in $locale', async ({ locale, button, pending, message }) => {
    const user = userEvent.setup();
    let finishRequest!: (response: Response) => void;
    const fetchMock = vi.spyOn(window, 'fetch').mockImplementation(() => new Promise<Response>((resolve) => {
      finishRequest = resolve;
    }));
    renderApp(locale);

    await user.click(await screen.findByRole('button', { name: button }));
    expect(screen.getByRole('button', { name: pending })).toBeDisabled();
    expect(screen.getByRole('button', { name: pending })).toHaveAttribute('aria-busy', 'true');
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText(/deploy|部署|v1\.2\.2/i)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(new URL(String(url)).pathname).toBe('/api/v1/services/billing-sync/commands');
    expect(new URL(String(url)).searchParams.get('environment')).toBe('prod');
    expect(init?.method).toBe('POST');
    expect(JSON.parse(String(init?.body))).toMatchObject({
      kind: 'sync_now', target_mode: 'all_online', offline_behavior: 'skip',
    });

    await act(async () => finishRequest(jsonResponse(accepted)));
    expect(await screen.findByText(message)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('button', { name: button })).toBeEnabled());
    expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
    expect(screen.queryByText(/deploy|部署|v1\.2\.2|sync completed|同步完成/i)).not.toBeInTheDocument();
  });

  it('clears pending state and shows the API error when dispatch fails', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'fetch').mockResolvedValue(jsonResponse({ detail: 'dispatch unavailable' }, 503));
    renderApp();

    await user.click(await screen.findByRole('button', { name: 'Sync Now' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Sync failed: dispatch unavailable');
    expect(screen.getByRole('button', { name: 'Sync Now' })).toBeEnabled();
    expect(screen.queryByText(/Sync command accepted/)).not.toBeInTheDocument();
  });

  it('reports no online targets without claiming command acceptance', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'fetch').mockResolvedValue(jsonResponse({
      ...accepted, noop_reason_code: 'no_online_instances', dispatched: [],
      counts: { dispatched: 0, queued: 0, skipped: 0, rejected: 0, total: 0 },
    }));
    renderApp();

    await user.click(await screen.findByRole('button', { name: 'Sync Now' }));
    expect(await screen.findByRole('alert')).toHaveTextContent(/no online instances/i);
    expect(screen.queryByText(/Sync command accepted/)).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Sync Now' })).toBeEnabled();
  });

  it('does not send a sync request for a service with no online instances', async () => {
    mockService(0);
    const fetchMock = vi.spyOn(window, 'fetch');
    renderApp();

    const button = await screen.findByRole('button', { name: 'Sync Now' });
    expect(button).toBeDisabled();
    await userEvent.setup().click(button);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

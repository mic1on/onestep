import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi, type MockInstance } from 'vitest';
import { I18nProvider } from '../i18n';
import NotificationSettingsPage from './NotificationSettingsPage';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/**
 * Route fetch by URL instead of by call order.
 *
 * The page loads channels, services and delivery history on mount, so an
 * order-based chain breaks whenever a new mount-time request is added. Routing by
 * URL keeps these tests about the channel form rather than about call sequencing.
 */
function mockFetchByUrl(createResponse: unknown) {
  return vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
    const url = String(input);
    const method = (init?.method ?? 'GET').toUpperCase();
    if (url.includes('/deliveries')) return jsonResponse({ items: [] });
    if (method === 'POST') return jsonResponse(createResponse);
    if (url.includes('/services')) return jsonResponse({ items: [] });
    if (url.includes('/channels')) return jsonResponse({ items: [] });
    return jsonResponse({});
  });
}

/** The fetch spy's type, derived from the function it replaces. */
type FetchSpy = MockInstance<typeof window.fetch>;

/** The request that actually created the channel (a POST to the channels route). */
function findCreateCall(fetchMock: FetchSpy) {
  const call = fetchMock.mock.calls.find(([input, init]) => {
    const method = ((init as RequestInit | undefined)?.method ?? 'GET').toUpperCase();
    return method === 'POST' && String(input).includes('/channels');
  });
  if (!call) throw new Error('no channel-create request was made');
  return JSON.parse(String((call[1] as RequestInit).body));
}

function renderPage() {
  return render(
    <I18nProvider initialLocale="en">
      <NotificationSettingsPage onAuthRequired={vi.fn()} onNotify={vi.fn()} />
    </I18nProvider>,
  );
}

describe('NotificationSettingsPage custom webhooks', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('saves a custom POST channel with query and body params', async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetchByUrl({
      id: 'channel-custom',
      name: 'ops-custom',
      provider: 'custom',
      webhook_url_masked: 'https://example.com/***',
      enabled: true,
      service_scopes: [],
      event_types: ['task_failed'],
      missed_start_grace_seconds: 300,
      custom_config: {
        method: 'POST',
        query_params: [{ key: 'service', value: '{{ service_name }}' }],
        body_params: [{ key: 'event', value: '{{ event_type }}' }],
      },
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    });

    renderPage();

    await user.type(await screen.findByLabelText(/name/i), 'ops-custom');
    await user.click(screen.getByRole('button', { name: /custom/i }));
    await user.type(screen.getByLabelText(/webhook url/i), 'https://example.com/hook');
    await user.click(screen.getByRole('button', { name: /add query param/i }));
    await user.type(screen.getByLabelText(/query parameter key 1/i), 'service');
    await user.click(screen.getByLabelText(/query parameter value 1/i));
    await user.paste('{{ service_name }}');
    await user.click(screen.getByRole('button', { name: /add body param/i }));
    await user.type(screen.getByLabelText(/body parameter key 1/i), 'event');
    await user.click(screen.getByLabelText(/body parameter value 1/i));
    await user.paste('{{ event_type }}');
    await user.click(screen.getByRole('button', { name: /create channel/i }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            ((init as RequestInit | undefined)?.method ?? 'GET') === 'POST' &&
            String(input).includes('/channels'),
        ),
      ).toBe(true),
    );
    const createBody = findCreateCall(fetchMock);
    expect(createBody.custom_config).toEqual({
      method: 'POST',
      query_params: [{ key: 'service', value: '{{ service_name }}' }],
      body_params: [{ key: 'event', value: '{{ event_type }}' }],
    });
  });

  it('picks a variable from the value field and fills an empty key', async () => {
    const user = userEvent.setup();
    const fetchMock = mockFetchByUrl({
      id: 'channel-custom-variable',
      name: 'ops-variable',
      provider: 'custom',
      webhook_url_masked: 'https://example.com/***',
      enabled: true,
      service_scopes: [],
      event_types: ['task_failed'],
      missed_start_grace_seconds: 300,
      custom_config: {
        method: 'POST',
        query_params: [],
        body_params: [{ key: 'service_environment', value: '{{ service_environment }}' }],
      },
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    });

    renderPage();

    await user.type(await screen.findByLabelText(/name/i), 'ops-variable');
    await user.click(screen.getByRole('button', { name: /custom/i }));
    await user.type(screen.getByLabelText(/webhook url/i), 'https://example.com/hook');
    await user.click(screen.getByRole('button', { name: /add body param/i }));
    await user.click(screen.getByRole('button', { name: /insert field/i }));
    await user.click(screen.getByRole('button', { name: 'service_environment' }));
    await user.click(screen.getByRole('button', { name: /create channel/i }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([input, init]) =>
            ((init as RequestInit | undefined)?.method ?? 'GET') === 'POST' &&
            String(input).includes('/channels'),
        ),
      ).toBe(true),
    );
    const createBody = findCreateCall(fetchMock);
    expect(createBody.custom_config.body_params).toEqual([
      { key: 'service_environment', value: '{{ service_environment }}' },
    ]);
  });

  it('marks notification refresh as busy while requests are pending', async () => {
    const pending = new Promise<Response>(() => undefined);
    vi.spyOn(window, 'fetch').mockReturnValue(pending);
    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Refreshing' }).getAttribute('aria-busy')).toBe('true');
    });
  });
  it('reports a failed test notification instead of claiming success', async () => {
    // Regression guard: the endpoint used to return "accepted" without sending
    // anything, and the console showed "Test accepted by {provider}". A broken
    // channel therefore looked healthy until a real incident.
    const onNotify = vi.fn();
    const channel = {
      id: 'channel-probe',
      name: 'ops-probe',
      provider: 'feishu' as const,
      webhook_url_masked: 'https://example.com/***',
      enabled: true,
      service_scopes: [],
      event_types: ['task_failed'],
      missed_start_grace_seconds: 300,
      custom_config: null,
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    };
    vi.spyOn(window, 'fetch').mockImplementation(async (input, init) => {
      const url = String(input);
      const method = (init?.method ?? 'GET').toUpperCase();
      if (url.includes('/deliveries')) return jsonResponse({ items: [] });
      if (url.includes('/test')) {
        return jsonResponse({
          status: 'accepted',
          channel_id: channel.id,
          provider: 'feishu',
          preview_text: 'probe',
          delivered: false,
          response_status_code: 500,
          error_message: 'webhook responded with status 500',
        });
      }
      if (url.includes('/services')) return jsonResponse({ items: [] });
      if (method === 'GET' && url.includes('/channels')) return jsonResponse({ items: [channel] });
      return jsonResponse({});
    });

    const user = userEvent.setup();
    render(
      <I18nProvider initialLocale="en">
        <NotificationSettingsPage onAuthRequired={vi.fn()} onNotify={onNotify} />
      </I18nProvider>,
    );

    await user.click(await screen.findByRole('button', { name: /test ops-probe/i }));

    await waitFor(() => {
      expect(onNotify).toHaveBeenCalledWith(
        expect.stringContaining('webhook responded with status 500'),
        'warn',
      );
    });
  });

  it('renders the delivery history with the real outcome', async () => {
    const channel = {
      id: 'channel-history',
      name: 'ops-history',
      provider: 'feishu' as const,
      webhook_url_masked: 'https://example.com/***',
      enabled: true,
      service_scopes: [],
      event_types: ['task_failed'],
      missed_start_grace_seconds: 300,
      custom_config: null,
      created_at: '2026-07-19T00:00:00Z',
      updated_at: '2026-07-19T00:00:00Z',
    };
    vi.spyOn(window, 'fetch').mockImplementation(async (input) => {
      const url = String(input);
      if (url.includes('/deliveries')) {
        return jsonResponse({
          items: [
            {
              id: 'delivery-1',
              channel_id: channel.id,
              event_type: 'task_failed',
              service_name: 'billing-sync',
              service_environment: 'prod',
              task_name: 'sync_users',
              status: 'failed',
              response_status_code: 500,
              error_message: 'webhook responded with status 500',
              created_at: '2026-07-19T00:00:00Z',
              sent_at: '2026-07-19T00:00:01Z',
            },
          ],
        });
      }
      if (url.includes('/services')) return jsonResponse({ items: [] });
      if (url.includes('/channels')) return jsonResponse({ items: [channel] });
      return jsonResponse({});
    });

    renderPage();

    expect(await screen.findByText('Delivery history')).toBeTruthy();
    expect(await screen.findByText('task_failed')).toBeTruthy();
    expect(await screen.findByText('failed')).toBeTruthy();
    expect(await screen.findByText(/HTTP 500/)).toBeTruthy();
  });
});

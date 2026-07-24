import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n';
import NotificationSettingsPage from './NotificationSettingsPage';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
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
    const fetchMock = vi
      .spyOn(window, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(
        jsonResponse({
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
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ items: [] }));

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

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    const createBody = JSON.parse(String((fetchMock.mock.calls[2][1] as RequestInit).body));
    expect(createBody.custom_config).toEqual({
      method: 'POST',
      query_params: [{ key: 'service', value: '{{ service_name }}' }],
      body_params: [{ key: 'event', value: '{{ event_type }}' }],
    });
  });

  it('picks a variable from the value field and fills an empty key', async () => {
    const user = userEvent.setup();
    const fetchMock = vi
      .spyOn(window, 'fetch')
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(
        jsonResponse({
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
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ items: [] }))
      .mockResolvedValueOnce(jsonResponse({ items: [] }));

    renderPage();

    await user.type(await screen.findByLabelText(/name/i), 'ops-variable');
    await user.click(screen.getByRole('button', { name: /custom/i }));
    await user.type(screen.getByLabelText(/webhook url/i), 'https://example.com/hook');
    await user.click(screen.getByRole('button', { name: /add body param/i }));
    await user.click(screen.getByRole('button', { name: /insert field/i }));
    await user.click(screen.getByRole('button', { name: 'service_environment' }));
    await user.click(screen.getByRole('button', { name: /create channel/i }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(5));
    const createBody = JSON.parse(String((fetchMock.mock.calls[2][1] as RequestInit).body));
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
});

import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  createNotificationChannel,
  deleteNotificationChannel,
  listNotificationChannels,
  listNotificationServices,
  setNotificationChannelEnabled,
  testNotificationChannel,
  updateNotificationChannel,
} from './api';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function calledPath(call: unknown[]) {
  return new URL(String(call[0])).pathname;
}

describe('notification api', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('loads notification channels and service options', async () => {
    const fetchMock = vi
      .spyOn(window, 'fetch')
      .mockResolvedValueOnce(
        jsonResponse({
          items: [
            {
              id: 'channel-1',
              name: 'ops-feishu',
              provider: 'feishu',
              webhook_url_masked: 'https://example.com/***',
              enabled: true,
              service_scopes: [],
              event_types: ['task_failed'],
              missed_start_grace_seconds: 300,
              created_at: '2026-07-16T00:00:00Z',
              updated_at: '2026-07-16T00:00:00Z',
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ name: 'billing-worker', environment: 'prod' }],
        }),
      );

    await expect(listNotificationChannels()).resolves.toHaveLength(1);
    await expect(listNotificationServices()).resolves.toEqual([
      { name: 'billing-worker', environment: 'prod' },
    ]);
    expect(calledPath(fetchMock.mock.calls[0])).toBe('/api/v1/settings/notifications/channels');
    expect(calledPath(fetchMock.mock.calls[1])).toBe('/api/v1/settings/notifications/services');
  });

  it('writes notification channel mutations with the backend contract', async () => {
    const channelBody = {
      id: 'channel-1',
      name: 'ops-feishu',
      provider: 'feishu',
      webhook_url_masked: 'https://example.com/***',
      enabled: true,
      service_scopes: [],
      event_types: ['task_failed'],
      missed_start_grace_seconds: 300,
      created_at: '2026-07-16T00:00:00Z',
      updated_at: '2026-07-16T00:00:00Z',
    };
    const fetchMock = vi
      .spyOn(window, 'fetch')
      .mockResolvedValueOnce(jsonResponse(channelBody))
      .mockResolvedValueOnce(jsonResponse({ ...channelBody, enabled: false }))
      .mockResolvedValueOnce(
        jsonResponse({
          status: 'accepted',
          channel_id: 'channel-1',
          provider: 'feishu',
          preview_text: 'ok',
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ status: 'deleted' }));

    await createNotificationChannel({
      name: 'ops-feishu',
      provider: 'feishu',
      webhook_url: 'https://example.com/hook',
      enabled: true,
      service_scopes: [],
      event_types: ['task_failed'],
      missed_start_grace_seconds: 300,
    });
    await setNotificationChannelEnabled('channel-1', false);
    await testNotificationChannel('channel-1');
    await deleteNotificationChannel('channel-1');

    expect(calledPath(fetchMock.mock.calls[0])).toBe('/api/v1/settings/notifications/channels');
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: 'POST' }));
    expect(calledPath(fetchMock.mock.calls[1])).toBe('/api/v1/settings/notifications/channels/channel-1/enabled');
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({ method: 'PATCH' }));
    expect(JSON.parse(String((fetchMock.mock.calls[1][1] as RequestInit).body))).toEqual({ enabled: false });
    expect(calledPath(fetchMock.mock.calls[2])).toBe('/api/v1/settings/notifications/channels/channel-1/test');
    expect(fetchMock.mock.calls[2][1]).toEqual(expect.objectContaining({ method: 'POST' }));
    expect(calledPath(fetchMock.mock.calls[3])).toBe('/api/v1/settings/notifications/channels/channel-1');
    expect(fetchMock.mock.calls[3][1]).toEqual(expect.objectContaining({ method: 'DELETE' }));
  });

  it('keeps the full notification channel patch endpoint for form edits', async () => {
    const channelBody = {
      id: 'channel-1',
      name: 'ops-feishu',
      provider: 'feishu',
      webhook_url_masked: 'https://example.com/***',
      enabled: true,
      service_scopes: [],
      event_types: ['task_failed'],
      missed_start_grace_seconds: 300,
      created_at: '2026-07-16T00:00:00Z',
      updated_at: '2026-07-16T00:00:00Z',
    };
    const fetchMock = vi.spyOn(window, 'fetch').mockResolvedValueOnce(jsonResponse(channelBody));

    await updateNotificationChannel('channel-1', { name: 'ops-alerts' });

    expect(calledPath(fetchMock.mock.calls[0])).toBe('/api/v1/settings/notifications/channels/channel-1');
    expect(fetchMock.mock.calls[0][1]).toEqual(expect.objectContaining({ method: 'PATCH' }));
    expect(JSON.parse(String((fetchMock.mock.calls[0][1] as RequestInit).body))).toEqual({
      name: 'ops-alerts',
    });
  });
});

import { beforeEach, describe, expect, it, vi } from 'vitest';
import { loadControlPlaneData, loadTaskMetricWindows } from './api';

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

const serviceSummary = {
  name: 'control-plane-demo',
  environment: 'dev',
  latest_deployment_version: '2026.7.16',
  service_status: 'online',
  latest_topology_hash: 'topology-demo',
  latest_sync_at: '2026-07-16T08:00:00Z',
  instance_count: 0,
  online_instance_count: 0,
  last_seen_at: null,
  source_kinds: ['memory_queue'],
  task_count: 1,
  created_at: '2026-07-16T08:00:00Z',
  updated_at: '2026-07-16T08:00:00Z',
};

describe('loadControlPlaneData', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('treats zero-online service data as offline for service and task state', async () => {
    vi.spyOn(window, 'fetch')
      .mockResolvedValueOnce(
        jsonResponse({
          items: [serviceSummary],
          total: 1,
          limit: 100,
          offset: 0,
          source_kind_counts: { memory_queue: 1 },
          summary: {
            total_services: 1,
            online_services: 0,
            attention_services: 0,
            offline_services: 1,
            ready_services: 0,
            total_instances: 0,
            online_instances: 0,
            total_tasks: 1,
            failing_tasks: 0,
          },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          service: serviceSummary,
          lookback_minutes: 60,
          lookback_started_at: '2026-07-16T07:00:00Z',
          task_count: 1,
          failing_task_count: 0,
          recent_events: [],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [
            {
              task_name: 'inspect_dead_letter',
              description: null,
              source_name: 'memory_queue',
              source_kind: 'memory_queue',
              source_config: { queue: 'control-plane-demo.dead-letter' },
              emit: [{ kind: 'handler', name: 'Handler', config: {} }],
              concurrency: 2,
              timeout_s: null,
              retry_policy: { kind: 'fixed', config: { attempts: 1 } },
              topology_hash: 'topology-task',
              metric_window_count: 0,
              latest_window_started_at: null,
              latest_window_ended_at: null,
              fetched: 0,
              started: 0,
              succeeded: 0,
              retried: 0,
              failed: 0,
              dead_lettered: 0,
              cancelled: 0,
              timeouts: 0,
              weighted_avg_duration_ms: null,
              max_p95_duration_ms: null,
              last_event_at: null,
              event_counts: {
                failed: 0,
                retried: 0,
                dead_lettered: 0,
                cancelled: 0,
                succeeded: 0,
              },
            },
          ],
          total: 1,
          limit: 100,
          offset: 0,
          lookback_minutes: 60,
          lookback_started_at: '2026-07-16T07:00:00Z',
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, limit: 100, offset: 0 }));

    const data = await loadControlPlaneData();

    expect(data.services[0]).toEqual(expect.objectContaining({ status: 'stopped', activeInstances: 0 }));
    expect(data.tasks[0]).toEqual(expect.objectContaining({ name: 'inspect_dead_letter', status: 'Offline' }));
  });

  it('loads recent metric windows from task detail telemetry', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockResolvedValueOnce(
      jsonResponse({
        service: serviceSummary,
        task_name: 'inspect_dead_letter',
        lookback_minutes: 60,
        lookback_started_at: '2026-07-16T07:00:00Z',
        summary: {
          task_name: 'inspect_dead_letter',
          description: null,
          source_name: 'memory_queue',
          source_kind: 'memory_queue',
          source_config: { queue: 'control-plane-demo.dead-letter' },
          emit: [{ kind: 'handler', name: 'Handler', config: {} }],
          concurrency: 2,
          timeout_s: null,
          retry_policy: { kind: 'fixed', config: { attempts: 1 } },
          topology_hash: 'topology-task',
          metric_window_count: 1,
          latest_window_started_at: '2026-07-16T07:59:00Z',
          latest_window_ended_at: '2026-07-16T08:00:00Z',
          fetched: 12,
          started: 12,
          succeeded: 11,
          retried: 0,
          failed: 1,
          dead_lettered: 0,
          cancelled: 0,
          timeouts: 0,
          weighted_avg_duration_ms: 42,
          max_p95_duration_ms: 84,
          last_event_at: null,
          event_counts: {
            failed: 1,
            retried: 0,
            dead_lettered: 0,
            cancelled: 0,
            succeeded: 11,
          },
        },
        task_control: { task_name: 'inspect_dead_letter', instances: [] },
        recent_metric_windows: [
          {
            instance_id: '11111111-1111-4111-8111-111111111111',
            task_name: 'inspect_dead_letter',
            window_id: 'inspect_dead_letter:one',
            window_started_at: '2026-07-16T07:59:00Z',
            window_ended_at: '2026-07-16T08:00:00Z',
            fetched: 12,
            started: 12,
            succeeded: 11,
            retried: 0,
            failed: 1,
            dead_lettered: 0,
            cancelled: 0,
            timeouts: 0,
            inflight: 2,
            avg_duration_ms: 42,
            p95_duration_ms: 84,
            received_at: '2026-07-16T08:00:01Z',
            created_at: '2026-07-16T08:00:01Z',
          },
        ],
        recent_events: [],
      }),
    );

    const windows = await loadTaskMetricWindows({
      id: 'control-plane-demo:dev:inspect_dead_letter',
      apiName: 'inspect_dead_letter',
      apiServiceName: 'control-plane-demo',
      environment: 'dev',
      serviceId: 'control-plane-demo:dev',
      name: 'inspect_dead_letter',
      status: 'Running',
      pipelineSource: 'memory_queue',
      pipelineSourceLabel: 'memory_queue',
      pipelineSink: 'handler',
      pipelineSinkLabel: 'Handler',
      concurrency: 2,
      retryAttempts: 1,
      uptime: '1m ago',
      throughputValue: '12/min',
      throughputNum: 12,
      successRate: 91.67,
      errorCount: 1,
      configYaml: '',
    });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/api/v1/services/control-plane-demo/tasks/inspect_dead_letter?environment=dev&lookback_minutes=60&metric_window_limit=24&event_limit=1',
    );
    expect(windows).toEqual([
      expect.objectContaining({
        window_id: 'inspect_dead_letter:one',
        fetched: 12,
        failed: 1,
        p95_duration_ms: 84,
      }),
    ]);
  });
});

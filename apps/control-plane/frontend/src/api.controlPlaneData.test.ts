import { beforeEach, describe, expect, it, vi } from 'vitest';
import {
  getFanoutCommandIds,
  loadControlPlaneData,
  loadTaskMetricWindows,
  loadTaskRecentLogs,
  pollTaskCommandCompletion,
} from './api';
import type { Task } from './types';

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
  service_status: 'offline',
  latest_topology_hash: 'topology-demo',
  latest_sync_at: '2026-07-16T08:00:00Z',
  instance_count: 0,
  online_instance_count: 0,
  last_seen_at: null,
  source_kinds: ['memory_queue'],
  task_count: 1,
  failing_task_count: 0,
  view_status: 'stopped',
  success_rate: 100,
  throughput_per_min: 0,
  error_count: 0,
  uptime_reference_at: null,
  online_task_count: 0,
  standby_instance_count: 0,
  created_at: '2026-07-16T08:00:00Z',
  updated_at: '2026-07-16T08:00:00Z',
};

const taskFixture: Task = {
  id: 'control-plane-demo:dev:inspect_dead_letter',
  apiName: 'inspect_dead_letter',
  apiServiceName: 'control-plane-demo',
  environment: 'dev',
  serviceId: 'control-plane-demo:dev',
  name: 'inspect_dead_letter',
  viewStatus: 'running',
  supportedCommands: ['pause_task', 'resume_task', 'restart_task'],
  pipelineSource: 'memory_queue',
  pipelineSourceLabel: 'memory_queue',
  sourceKind: 'memory_queue',
  sourceConfig: null,
  sourceName: 'memory_queue',
  pipelineSink: 'handler',
  pipelineSinkLabel: 'Handler',
  sinkKind: 'handler',
  sinkConfig: null,
  sinkName: 'Handler',
  concurrency: 2,
  retryAttempts: 1,
  uptimeReferenceAt: null,
  throughputPerMin: 12,
  successRate: 91.67,
  errorCount: 1,
  configYaml: '',
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
              pause_requested: null,
              event_counts: {
                started: 0,
                failed: 0,
                retried: 0,
                dead_lettered: 0,
                cancelled: 0,
                succeeded: 0,
              },
              view_status: 'offline',
              success_rate: 100,
              throughput_per_min: 0,
              error_count: 0,
              retry_attempts: 1,
              source_label: 'control-plane-demo.dead-letter',
              sink_label: 'Handler',
              config_yaml: '',
              uptime_reference_at: null,
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

    expect(data.services[0]).toEqual(expect.objectContaining({ viewStatus: 'stopped', activeInstances: 0 }));
    expect(data.tasks[0]).toEqual(expect.objectContaining({ name: 'inspect_dead_letter', viewStatus: 'offline' }));
  });

  it('passes raw source_kind / source_config / source_name through to the Task', async () => {
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
              task_name: 'sync_orders',
              description: null,
              source_name: 'mysql.orders',
              source_kind: 'mysql_incremental',
              source_config: { table: 'orders', key: 'id', cursor: 'updated_at', batch_size: 500 },
              emit: [{ kind: 'mysql_table_sink', name: 'mysql.audit', config: { table: 'orders_audit', mode: 'upsert', keys: 'id' } }],
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
              pause_requested: null,
              event_counts: {
                started: 0,
                failed: 0,
                retried: 0,
                dead_lettered: 0,
                cancelled: 0,
                succeeded: 0,
              },
              view_status: 'offline',
              success_rate: 100,
              throughput_per_min: 0,
              error_count: 0,
              retry_attempts: 1,
              source_label: 'mysql.orders',
              sink_label: 'mysql.audit',
              config_yaml: '',
              uptime_reference_at: null,
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

    // The full source_config must survive mapTask() so the topology panel can
    // render per-kind fields instead of hardcoded Kafka metadata.
    expect(data.tasks[0]).toEqual(
      expect.objectContaining({
        sourceKind: 'mysql_incremental',
        sourceName: 'mysql.orders',
        sourceConfig: { table: 'orders', key: 'id', cursor: 'updated_at', batch_size: 500 },
        sinkKind: 'mysql_table_sink',
        sinkName: 'mysql.audit',
        sinkConfig: { table: 'orders_audit', mode: 'upsert', keys: 'id' },
      }),
    );
  });

  it('passes raw source_kind / source_config / source_name through to the Task', async () => {
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
              task_name: 'sync_orders',
              description: null,
              source_name: 'mysql.orders',
              source_kind: 'mysql_incremental',
              source_config: { table: 'orders', key: 'id', cursor: 'updated_at', batch_size: 500 },
              emit: [{ kind: 'mysql_table_sink', name: 'mysql.audit', config: { table: 'orders_audit', mode: 'upsert', keys: 'id' } }],
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
              supported_commands: ['pause_task', 'resume_task'],
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

    // The full source_config must survive mapTask() so the topology panel can
    // render per-kind fields instead of hardcoded Kafka metadata.
    expect(data.tasks[0]).toEqual(
      expect.objectContaining({
        sourceKind: 'mysql_incremental',
        sourceName: 'mysql.orders',
        sourceConfig: { table: 'orders', key: 'id', cursor: 'updated_at', batch_size: 500 },
        supportedCommands: ['pause_task', 'resume_task'],
        sinkKind: 'mysql_table_sink',
        sinkName: 'mysql.audit',
        sinkConfig: { table: 'orders_audit', mode: 'upsert', keys: 'id' },
      }),
    );
  });

  it('maps failed task events into logs with exception details and tracebacks', async () => {
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
            failing_tasks: 1,
          },
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          service: serviceSummary,
          lookback_minutes: 60,
          lookback_started_at: '2026-07-16T07:00:00Z',
          task_count: 1,
          failing_task_count: 1,
          recent_events: [
            {
              event_id: 'evt_failed',
              instance_id: '11111111-1111-4111-8111-111111111111',
              task_name: 'inspect_dead_letter',
              kind: 'failed',
              occurred_at: '2026-07-16T08:00:00Z',
              attempts: 1,
              duration_ms: 42,
              failure_kind: 'error',
              exception_type: 'RuntimeError',
              message: 'boom',
              traceback: 'Traceback (most recent call last):\nRuntimeError: boom\n',
              meta: { source: 'interval:5s' },
              received_at: '2026-07-16T08:00:01Z',
              created_at: '2026-07-16T08:00:01Z',
              level: 'error',
            },
          ],
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [],
          total: 0,
          limit: 100,
          offset: 0,
          lookback_minutes: 60,
          lookback_started_at: '2026-07-16T07:00:00Z',
        }),
      )
      .mockResolvedValueOnce(jsonResponse({ items: [], total: 0, limit: 100, offset: 0 }));

    const data = await loadControlPlaneData();

    expect(data.logs[0]).toEqual(
      expect.objectContaining({
        level: 'error',
        source: 'inspect_dead_letter',
        message: 'RuntimeError: boom',
        eventKind: 'failed',
        attempts: 1,
        durationMs: 42,
        instanceId: '11111111-1111-4111-8111-111111111111',
        sourceDetail: 'interval:5s',
        exceptionType: 'RuntimeError',
        failureKind: 'error',
        traceback: 'Traceback (most recent call last):\nRuntimeError: boom\n',
      }),
    );
  });

  it('loads recent metric chart points from task detail telemetry', async () => {
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
          pause_requested: false,
          event_counts: {
            started: 12,
            failed: 1,
            retried: 0,
            dead_lettered: 0,
            cancelled: 0,
            succeeded: 11,
          },
          view_status: 'failed',
          success_rate: 91.67,
          throughput_per_min: 0,
          error_count: 1,
          retry_attempts: 1,
          source_label: 'control-plane-demo.dead-letter',
          sink_label: 'Handler',
          config_yaml: '',
          uptime_reference_at: '2026-07-16T07:59:00Z',
        },
        task_control: { task_name: 'inspect_dead_letter', instances: [] },
        recent_metric_points: [
          {
            bucket_started_at: '2026-07-16T07:58:00Z',
            bucket_ended_at: '2026-07-16T07:59:00Z',
            reported_window_count: 0,
            fetched: 0,
            started: 0,
            succeeded: 0,
            retried: 0,
            failed: 0,
            dead_lettered: 0,
            cancelled: 0,
            timeouts: 0,
            inflight: 0,
            avg_duration_ms: null,
            p95_duration_ms: null,
          },
          {
            bucket_started_at: '2026-07-16T07:59:00Z',
            bucket_ended_at: '2026-07-16T08:00:00Z',
            reported_window_count: 1,
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
          },
        ],
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

    const points = await loadTaskMetricWindows(taskFixture, 30);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/api/v1/services/control-plane-demo/tasks/inspect_dead_letter?environment=dev&lookback_minutes=30&metric_window_limit=24&event_limit=1',
    );
    expect(points).toEqual([
      expect.objectContaining({
        bucket_started_at: '2026-07-16T07:58:00Z',
        reported_window_count: 0,
        fetched: 0,
      }),
      expect.objectContaining({
        bucket_started_at: '2026-07-16T07:59:00Z',
        reported_window_count: 1,
        fetched: 12,
        failed: 1,
        p95_duration_ms: 84,
      }),
    ]);
  });

  it('loads recent task logs from task detail events', async () => {
    const fetchMock = vi.spyOn(window, 'fetch').mockResolvedValueOnce(
      jsonResponse({
        recent_events: [
          {
            event_id: 'evt_task_failed',
            instance_id: '11111111-1111-4111-8111-111111111111',
            task_name: 'inspect_dead_letter',
            kind: 'failed',
            occurred_at: '2026-07-16T08:00:00Z',
            attempts: 1,
            duration_ms: 42,
            failure_kind: 'error',
            exception_type: 'RuntimeError',
            message: 'boom',
            traceback: 'Traceback (most recent call last):\nRuntimeError: boom\n',
            meta: { source: 'interval:5s' },
            received_at: '2026-07-16T08:00:01Z',
            created_at: '2026-07-16T08:00:01Z',
            level: 'error',
          },
        ],
      }),
    );

    const logs = await loadTaskRecentLogs(taskFixture, 30, 10);

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/api/v1/services/control-plane-demo/tasks/inspect_dead_letter?environment=dev&lookback_minutes=30&metric_window_limit=1&event_limit=10',
    );
    expect(logs).toEqual([
      expect.objectContaining({
        level: 'error',
        message: 'RuntimeError: boom',
        eventKind: 'failed',
        attempts: 1,
        durationMs: 42,
        instanceId: '11111111-1111-4111-8111-111111111111',
        sourceDetail: 'interval:5s',
        traceback: 'Traceback (most recent call last):\nRuntimeError: boom\n',
      }),
    ]);
  });

  it('extracts dispatched and queued command ids from fanout responses', () => {
    expect(
      getFanoutCommandIds({
        kind: 'restart_task',
        target_mode: 'all_online',
        offline_behavior: 'skip',
        noop_reason_code: null,
        noop_reason_message: null,
        counts: { dispatched: 1, queued: 1, skipped: 0, rejected: 0, total: 2 },
        dispatched: [
          {
            instance_id: '11111111-1111-4111-8111-111111111111',
            node_name: 'worker-a',
            connectivity: 'online',
            session_id: 'session-a',
            command_id: 'command-a',
            outcome: 'dispatched',
            reason_code: null,
            reason_message: null,
          },
        ],
        queued: [
          {
            instance_id: '22222222-2222-4222-8222-222222222222',
            node_name: 'worker-b',
            connectivity: 'offline',
            session_id: null,
            command_id: 'command-b',
            outcome: 'queued',
            reason_code: null,
            reason_message: null,
          },
        ],
        skipped: [],
        rejected: [],
      }),
    ).toEqual(['command-a', 'command-b']);
  });

  it('polls restart task commands until they succeed', async () => {
    const fetchMock = vi
      .spyOn(window, 'fetch')
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ command_id: 'command-a', kind: 'restart_task', status: 'dispatched', error_code: null, error_message: null, updated_at: '2026-07-16T08:00:00Z' }],
          total: 1,
          limit: 100,
          offset: 0,
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse({
          items: [{ command_id: 'command-a', kind: 'restart_task', status: 'succeeded', error_code: null, error_message: null, updated_at: '2026-07-16T08:00:01Z' }],
          total: 1,
          limit: 100,
          offset: 0,
        }),
      );

    const result = await pollTaskCommandCompletion(taskFixture, ['command-a'], {
      intervalMs: 0,
      timeoutMs: 100,
    });

    expect(result).toEqual({ completed: true, failed: false });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(String(fetchMock.mock.calls[0][0])).toContain(
      '/api/v1/services/control-plane-demo/commands?environment=dev&kind=restart_task&limit=100&offset=0',
    );
  });

  it('reports failed restart task command completion', async () => {
    vi.spyOn(window, 'fetch').mockResolvedValueOnce(
      jsonResponse({
        items: [{ command_id: 'command-a', kind: 'restart_task', status: 'failed', error_code: 'restart_failed', error_message: 'boom', updated_at: '2026-07-16T08:00:00Z' }],
        total: 1,
        limit: 100,
        offset: 0,
      }),
    );

    const result = await pollTaskCommandCompletion(taskFixture, ['command-a'], {
      intervalMs: 0,
      timeoutMs: 100,
    });

    expect(result).toEqual({ completed: true, failed: true });
  });
});

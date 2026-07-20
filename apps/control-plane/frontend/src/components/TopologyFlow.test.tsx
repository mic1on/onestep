import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { I18nProvider } from '../i18n';
import type { Task } from '../types';
import TopologyFlow, {
  getTopologyFlowDurationSeconds,
  getTopologySourceLabel,
  isTopologyFlowActive,
} from './TopologyFlow';

const baseTask: Task = {
  id: 'svc:dev:produce_and_store',
  apiName: 'produce_and_store',
  apiServiceName: 'svc',
  environment: 'dev',
  serviceId: 'svc:dev',
  name: 'produce_and_store',
  viewStatus: 'running',
  supportedCommands: ['pause_task', 'restart_task'],
  pipelineSource: 'interval',
  pipelineSourceLabel: 'interval:5s',
  sourceKind: 'interval',
  sourceConfig: { seconds: 5 },
  sourceName: 'interval',
  pipelineSink: 'mysql_table_sink',
  pipelineSinkLabel: 'mysql.table_sink:cp_demo_events',
  sinkKind: 'mysql_table_sink',
  sinkConfig: { table: 'cp_demo_events', mode: 'upsert', keys: 'event_id' },
  sinkName: 'mysql.table_sink:cp_demo_events',
  concurrency: 1,
  retryAttempts: 3,
  uptimeReferenceAt: null,
  throughputPerMin: 120,
  successRate: 100,
  errorCount: 0,
  configYaml: '',
};

function renderTopology(overrides: Partial<Task> = {}) {
  const task = { ...baseTask, ...overrides };
  return render(
    <I18nProvider initialLocale="en">
      <TopologyFlow task={task} />
    </I18nProvider>,
  );
}

describe('topology flow motion', () => {
  it('activates flow only for running tasks with positive throughput', () => {
    expect(isTopologyFlowActive(baseTask)).toBe(true);
    expect(isTopologyFlowActive({ ...baseTask, viewStatus: 'paused' })).toBe(false);
    expect(isTopologyFlowActive({ ...baseTask, throughputPerMin: 0 })).toBe(false);
  });

  it('maps higher throughput to faster clamped durations', () => {
    expect(getTopologyFlowDurationSeconds(0)).toBe(2.4);
    expect(getTopologyFlowDurationSeconds(12)).toBeLessThan(getTopologyFlowDurationSeconds(1));
    expect(getTopologyFlowDurationSeconds(120)).toBe(0.75);
    expect(getTopologyFlowDurationSeconds(1000)).toBe(0.75);
  });

  it('renders active connectors for flowing tasks', () => {
    renderTopology({ throughputPerMin: 120 });

    const diagram = screen.getByTestId('topology-flow-diagram') as HTMLElement;
    const sourceConnectorFrame = screen.getByTestId('topology-source-connector-frame') as HTMLElement;
    expect(diagram.dataset.flowing).toBe('true');
    expect(diagram.style.getPropertyValue('--topology-flow-duration')).toBe('0.75s');
    expect(sourceConnectorFrame.className).toContain('sm:self-start');
    expect(sourceConnectorFrame.className).toContain('sm:h-20');
    expect(sourceConnectorFrame.className).not.toContain('sm:mt-7');
    expect(screen.getByTestId('topology-source-connector').dataset.flowing).toBe('true');
    expect(screen.getByTestId('topology-sink-connector').dataset.flowing).toBe('true');
    expect(screen.getAllByTestId('topology-flow-packet')).toHaveLength(4);
  });

  it('keeps connectors static for paused tasks', () => {
    renderTopology({ viewStatus: 'paused', throughputPerMin: 120 });

    const diagram = screen.getByTestId('topology-flow-diagram') as HTMLElement;
    expect(diagram.dataset.flowing).toBe('false');
    expect(screen.getByTestId('topology-source-connector').dataset.flowing).toBe('false');
    expect(screen.queryAllByTestId('topology-flow-packet')).toHaveLength(0);
  });
});

describe('topology source label', () => {
  it('shows only the core queue name for SQS URLs', () => {
    const sqsUrl = 'https://sqs.cn-northwest-1.amazonaws.com.cn/928507961548/ceegic-bidding-signup.fifo';
    const task = {
      ...baseTask,
      pipelineSource: 'sqs_queue',
      pipelineSourceLabel: sqsUrl,
      sourceKind: 'sqs_queue',
      sourceConfig: { url: sqsUrl },
      sourceName: sqsUrl,
    };

    expect(getTopologySourceLabel(task)).toBe('ceegic-bidding-signup.fifo');

    renderTopology(task);

    expect(screen.getByText('ceegic-bidding-signup.fifo')).toBeTruthy();
    expect(screen.queryByText(sqsUrl)).toBeNull();
  });
});

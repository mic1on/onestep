import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider } from '../i18n';
import type { Task, TaskCommandKind } from '../types';
import TasksList from './TasksList';

const baseTask: Task = {
  id: 'ceegic:prod:sync_user_record',
  apiName: 'sync_user_record',
  apiServiceName: 'ceegic',
  environment: 'prod',
  serviceId: 'ceegic:prod',
  name: 'sync_user_record',
  description: null,
  viewStatus: 'idle',
  supportedCommands: [],
  pipelineSource: 'cron',
  pipelineSourceLabel: 'cron',
  sourceKind: 'cron',
  sourceConfig: null,
  sourceName: 'cron',
  pipelineSink: 'handler',
  pipelineSinkLabel: 'handler',
  sinkKind: 'handler',
  sinkConfig: null,
  sinkName: 'handler',
  concurrency: 1,
  retryAttempts: 1,
  uptimeReferenceAt: null,
  throughputPerMin: 0,
  successRate: 100,
  errorCount: 0,
  configYaml: '',
};

function renderTasksList(task: Task) {
  return renderTasks([task]);
}

function renderTasks(tasks: Task[], pendingTaskId: string | null = null) {
  return render(
    <I18nProvider initialLocale="en">
      <TasksList
        tasks={tasks}
        onTaskSelect={vi.fn()}
        onRestartTask={vi.fn()}
        onToggleTaskStatus={vi.fn()}
        pendingTaskId={pendingTaskId}
      />
    </I18nProvider>,
  );
}

describe('TasksList status colors', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('renders idle tasks with the same neutral tone as the task detail header', () => {
    renderTasksList(baseTask);

    const idleLabel = screen.getByText('Idle');
    expect(idleLabel.className).toContain('text-slate-600');
    expect(idleLabel.className).not.toContain('text-amber-600');
  });

  it('renders task descriptions when present', () => {
    renderTasksList({
      ...baseTask,
      description: 'Forwards approved records into the HTTP sink.',
    });

    expect(screen.getByText('Forwards approved records into the HTTP sink.')).toBeTruthy();
  });

  it('keeps failed tasks in the amber attention tone', () => {
    renderTasksList({ ...baseTask, id: 'ceegic:prod:sync_failed', viewStatus: 'failed' });

    expect(screen.getByText('Failed').className).toContain('text-amber-600');
  });

  it('shows only the core queue name for SQS URLs in pipeline cards', () => {
    const sqsUrl = 'https://sqs.cn-northwest-1.amazonaws.com.cn/928507961548/ceegic-bidding-signup.fifo';

    renderTasksList({
      ...baseTask,
      pipelineSource: 'sqs_queue',
      pipelineSourceLabel: sqsUrl,
      sourceKind: 'sqs_queue',
      sourceConfig: { url: sqsUrl },
      sourceName: sqsUrl,
    });

    expect(screen.getByText('ceegic-bidding-signup.fifo')).toBeTruthy();
    expect(screen.queryByText(sqsUrl)).toBeNull();
  });

  it('keeps pending actions local and restores focus after Escape', () => {
    const first = {
      ...baseTask,
      id: 'service:prod:first',
      name: 'first',
      viewStatus: 'running' as const,
      supportedCommands: ['pause_task', 'restart_task'] as TaskCommandKind[],
    };
    const second = {
      ...first,
      id: 'service:prod:second',
      name: 'second',
    };
    renderTasks([first, second], 'service:prod:first');

    const triggers = screen.getAllByRole('button', { name: /More actions for/ });
    fireEvent.click(triggers[0]);
    expect(screen.getAllByText('Processing...').length).toBeGreaterThan(0);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(document.activeElement).toBe(triggers[0]);

    fireEvent.click(triggers[1]);
    expect(screen.queryByText('Processing...')).toBeNull();
    const pause = screen.getByRole('menuitem', { name: 'Pause' }) as HTMLButtonElement;
    expect(pause.disabled).toBe(false);
  });
});

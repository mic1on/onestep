import { describe, expect, it } from 'vitest';
import { getHeaderStatus, getTaskToggleCommand, isTaskToggleSupported } from './App';
import type { Task, TaskCommandKind } from './types';

const labels: Record<string, string> = {
  'common.running': 'RUNNING',
  'common.degraded': 'DEGRADED',
  'common.offline': 'OFFLINE',
  'status.idle': 'Idle',
  'status.paused': 'Paused',
};

const t = (key: string) => labels[key] ?? key;

function task(viewStatus: Task['viewStatus'], supportedCommands: TaskCommandKind[]): Task {
  return {
    id: `service:prod:${viewStatus}`,
    serviceId: 'service:prod',
    name: `${viewStatus}_task`,
    description: null,
    viewStatus,
    supportedCommands,
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
}

describe('getHeaderStatus', () => {
  it('labels idle task detail pages as idle instead of offline', () => {
    expect(getHeaderStatus('running', 'idle', t).label).toBe('Idle');
  });

  it('keeps stopped services labelled offline when no task is selected', () => {
    expect(getHeaderStatus('stopped', undefined, t).label).toBe('OFFLINE');
  });
});

describe('task toggle command mapping', () => {
  it('allows idle and failed tasks to dispatch pause_task when the runtime supports it', () => {
    const idleTask = task('idle', ['pause_task', 'resume_task', 'restart_task']);
    const failedTask = task('failed', ['pause_task']);

    expect(getTaskToggleCommand(idleTask)).toBe('pause_task');
    expect(isTaskToggleSupported(idleTask)).toBe(true);
    expect(getTaskToggleCommand(failedTask)).toBe('pause_task');
    expect(isTaskToggleSupported(failedTask)).toBe(true);
  });

  it('keeps paused tasks resumable and offline tasks without a toggle command', () => {
    const pausedTask = task('paused', ['pause_task', 'resume_task']);
    const offlineTask = task('offline', ['pause_task', 'resume_task']);

    expect(getTaskToggleCommand(pausedTask)).toBe('resume_task');
    expect(isTaskToggleSupported(pausedTask)).toBe(true);
    expect(getTaskToggleCommand(offlineTask)).toBeNull();
    expect(isTaskToggleSupported(offlineTask)).toBe(false);
  });
});

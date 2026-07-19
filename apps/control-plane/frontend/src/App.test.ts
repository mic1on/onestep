import { describe, expect, it } from 'vitest';
import { getHeaderStatus } from './App';

const labels: Record<string, string> = {
  'common.running': 'RUNNING',
  'common.degraded': 'DEGRADED',
  'common.offline': 'OFFLINE',
  'status.idle': 'Idle',
  'status.paused': 'Paused',
};

const t = (key: string) => labels[key] ?? key;

describe('getHeaderStatus', () => {
  it('labels idle task detail pages as idle instead of offline', () => {
    expect(getHeaderStatus('running', 'idle', t).label).toBe('Idle');
  });

  it('keeps stopped services labelled offline when no task is selected', () => {
    expect(getHeaderStatus('stopped', undefined, t).label).toBe('OFFLINE');
  });
});

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { I18nProvider } from '../i18n';
import type { LogEntry } from '../types';
import TaskEventDiagnostics from './TaskEventDiagnostics';

const failedLog: LogEntry = {
  timestamp: '08:00:00',
  level: 'error',
  source: 'inspect_dead_letter',
  message: 'RuntimeError: boom',
  eventKind: 'failed',
  attempts: 3,
  durationMs: 421,
  instanceId: 'b1b097c9-374e-4694-8017-2af0d3f988c3',
  sourceDetail: 'interval:5s',
  exceptionType: 'RuntimeError',
  failureKind: 'error',
  traceback: 'Traceback (most recent call last):\nRuntimeError: boom\n',
};

const firstAttemptSuccessLog: LogEntry = {
  timestamp: '08:00:01',
  level: 'info',
  source: 'produce_and_store',
  message: 'succeeded',
  eventKind: 'succeeded',
  attempts: 0,
  durationMs: 38,
  instanceId: 'b1b097c9-374e-4694-8017-2af0d3f988c3',
  sourceDetail: 'interval:5s',
};

function renderDiagnostics(logs: LogEntry[]) {
  return render(
    <I18nProvider initialLocale="en">
      <TaskEventDiagnostics logs={logs} isLoading={false} error={null} />
    </I18nProvider>,
  );
}

describe('TaskEventDiagnostics', () => {
  it('renders failed event summaries with a traceback disclosure', () => {
    renderDiagnostics([failedLog]);

    expect(screen.getByText('RuntimeError: boom')).toBeTruthy();
    expect(screen.getByText('inspect_dead_letter')).toBeTruthy();
    expect(screen.getByText('failed')).toBeTruthy();
    expect(screen.getByText('421ms')).toBeTruthy();
    expect(screen.getByText('attempt 3')).toBeTruthy();
    expect(screen.getByText('source:interval:5s')).toBeTruthy();
    expect(screen.getByText('inst:b1b097c9')).toBeTruthy();
    expect(screen.getByText('Traceback')).toBeTruthy();
    expect(screen.getByText(/Traceback \(most recent call last\)/)).toBeTruthy();
  });

  it('hides attempt zero for non-retry events', () => {
    renderDiagnostics([firstAttemptSuccessLog]);

    expect(screen.queryByText('attempt 0')).toBeNull();
    expect(screen.getByText('38ms')).toBeTruthy();
  });
});

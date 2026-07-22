import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { I18nProvider, type Locale } from '../i18n';
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

const restartCommandLog: LogEntry = {
  id: 'command:cmd_restart_task',
  timestamp: '08:00:02',
  level: 'info',
  source: 'inspect_dead_letter',
  sourceType: 'command',
  message: 'succeeded',
  eventKind: 'restart_task',
  durationMs: 1200,
  instanceId: 'b1b097c9-374e-4694-8017-2af0d3f988c3',
  commandId: 'cmd_restart_task',
  commandStatus: 'succeeded',
  ackStatus: 'accepted',
};

function renderDiagnostics(
  logs: LogEntry[],
  props: Partial<{
    lookbackMinutes: number;
    onLookbackMinutesChange: (minutes: number) => void;
    total: number;
    limit: number;
    offset: number;
    onPageChange: (offset: number) => void;
    locale: Locale;
  }> = {},
) {
  return render(
    <I18nProvider initialLocale={props.locale ?? 'en'}>
      <TaskEventDiagnostics
        logs={logs}
        isLoading={false}
        error={null}
        lookbackMinutes={props.lookbackMinutes ?? 15}
        onLookbackMinutesChange={props.onLookbackMinutesChange ?? vi.fn()}
        total={props.total ?? logs.length}
        limit={props.limit ?? 20}
        offset={props.offset ?? 0}
        onPageChange={props.onPageChange ?? vi.fn()}
      />
    </I18nProvider>,
  );
}

describe('TaskEventDiagnostics', () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  it('renders failed event summaries with a traceback disclosure', () => {
    renderDiagnostics([failedLog]);

    expect(screen.getByText('Task Events')).toBeTruthy();
    expect(screen.getByText('RuntimeError: boom')).toBeTruthy();
    expect(screen.getByText('inspect_dead_letter')).toBeTruthy();
    expect(screen.getByText('Failed')).toBeTruthy();
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

  it('renders command events with command metadata chips', () => {
    renderDiagnostics([restartCommandLog]);

    expect(screen.getByText('Restart task')).toBeTruthy();
    expect(screen.getByText('succeeded')).toBeTruthy();
    expect(screen.getByText('command')).toBeTruthy();
    expect(screen.getByText('status:Succeeded')).toBeTruthy();
    expect(screen.getByText('ack:Accepted')).toBeTruthy();
    expect(screen.getByText('cmd:cmd_rest')).toBeTruthy();
    expect(screen.getByText('1200ms')).toBeTruthy();
  });

  it('localizes event and command status chips in Chinese', () => {
    renderDiagnostics([{ ...restartCommandLog, eventKind: 'pause_task' }], {
      locale: 'zh-CN',
    });

    expect(screen.getByText('暂停任务')).toBeTruthy();
    expect(screen.getByText('命令')).toBeTruthy();
    expect(screen.getByText('状态：成功')).toBeTruthy();
    expect(screen.getByText('确认：已接受')).toBeTruthy();
    expect(screen.getByText('命令：cmd_rest')).toBeTruthy();
    expect(screen.getByText('实例：b1b097c9')).toBeTruthy();
  });

  it('changes the event lookback from preset and custom controls', () => {
    const handleLookbackChange = vi.fn();
    renderDiagnostics([firstAttemptSuccessLog], {
      lookbackMinutes: 15,
      onLookbackMinutesChange: handleLookbackChange,
    });

    fireEvent.click(screen.getByRole('button', { name: '30m' }));
    fireEvent.change(screen.getByLabelText('Lookback minutes'), { target: { value: '45' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

    expect(handleLookbackChange).toHaveBeenCalledWith(30);
    expect(handleLookbackChange).toHaveBeenCalledWith(45);
  });

  it('changes pages with server offsets', () => {
    const handlePageChange = vi.fn();
    renderDiagnostics([failedLog], {
      total: 45,
      limit: 20,
      offset: 20,
      onPageChange: handlePageChange,
    });

    expect(screen.getByText('Showing 21 to 40 of 45 events')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Previous page' }));
    fireEvent.click(screen.getByRole('button', { name: 'Next page' }));

    expect(handlePageChange).toHaveBeenCalledWith(0);
    expect(handlePageChange).toHaveBeenCalledWith(40);
  });

  it('announces task event loading', () => {
    render(
      <I18nProvider initialLocale="en">
        <TaskEventDiagnostics
          error={null}
          isLoading
          limit={20}
          logs={[]}
          lookbackMinutes={15}
          offset={0}
          onLookbackMinutesChange={vi.fn()}
          onPageChange={vi.fn()}
          total={0}
        />
      </I18nProvider>,
    );

    expect(screen.getByRole('status').textContent).toContain('Loading task events...');
  });
});

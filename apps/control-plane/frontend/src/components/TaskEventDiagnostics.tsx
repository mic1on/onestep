import { useCallback, useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronLeft, ChevronRight, ListFilter, Terminal } from 'lucide-react';
import {
  MAX_TASK_EVENT_LOOKBACK_MINUTES,
  TASK_EVENT_LOOKBACK_PRESETS,
} from '../api';
import type { TaskEventHistoryKind } from '../api';
import type { LogEntry } from '../types';
import { useI18n, type MessageKey } from '../i18n';
import LookbackControl from './LookbackControl';
import useDismissibleMenu from './useDismissibleMenu';

const RUNTIME_EVENT_KINDS: readonly TaskEventHistoryKind[] = [
  'started',
  'succeeded',
  'failed',
  'retried',
  'dead_lettered',
  'cancelled',
];

const COMMAND_EVENT_KINDS: readonly TaskEventHistoryKind[] = [
  'pause_task',
  'resume_task',
  'restart_task',
  'discard_dead_letters',
  'replay_dead_letters',
  'run_task_once',
];

interface TaskEventDiagnosticsProps {
  logs: LogEntry[];
  isLoading: boolean;
  error: string | null;
  lookbackMinutes: number;
  onLookbackMinutesChange: (minutes: number) => void;
  total: number;
  limit: number;
  offset: number;
  onPageChange: (offset: number) => void;
  selectedKinds: readonly TaskEventHistoryKind[];
  onSelectedKindsChange: (kinds: TaskEventHistoryKind[]) => void;
}

export default function TaskEventDiagnostics({
  logs,
  isLoading,
  error,
  lookbackMinutes,
  onLookbackMinutesChange,
  total,
  limit,
  offset,
  onPageChange,
  selectedKinds,
  onSelectedKindsChange,
}: TaskEventDiagnosticsProps) {
  const { t } = useI18n();
  const start = total === 0 ? 0 : offset + 1;
  const end = Math.min(offset + limit, total);
  const canGoPrevious = offset > 0;
  const canGoNext = offset + limit < total;

  return (
    <div className="overflow-visible rounded-xl border border-slate-200 bg-white shadow-xs">
      <div className="rounded-t-xl border-b border-slate-200 bg-slate-50 px-5 py-4">
        <div className="flex flex-wrap items-center gap-3">
          <h3 className="flex items-center gap-2 text-xs font-bold text-slate-700">
            <Terminal className="h-4 w-4 text-slate-400" />
            <span>{t('task.events')}</span>
          </h3>

          <LookbackControl
            ariaLabel={`${t('task.events')} ${t('chart.lookbackMinutes')}`}
            lookbackMinutes={lookbackMinutes}
            maxLookbackMinutes={MAX_TASK_EVENT_LOOKBACK_MINUTES}
            presets={TASK_EVENT_LOOKBACK_PRESETS}
            onLookbackMinutesChange={onLookbackMinutesChange}
          />

          <TaskEventKindFilter
            selectedKinds={selectedKinds}
            onSelectedKindsChange={onSelectedKindsChange}
          />
        </div>
      </div>

      <div className="p-4 space-y-3">
        {isLoading ? (
          <div
            aria-live="polite"
            className="ui-panel-state-enter text-xs font-semibold text-slate-500"
            role="status"
          >
            {t('task.loadingEvents')}
          </div>
        ) : error ? (
          <div className="ui-panel-state-enter flex items-center gap-2 text-xs font-semibold text-rose-600" role="alert">
            <AlertTriangle className="h-4 w-4" />
            <span>{error}</span>
          </div>
        ) : logs.length === 0 ? (
          <div className="ui-panel-state-enter text-xs font-semibold text-slate-500" role="status">
            {t('task.noEvents')}
          </div>
        ) : (
          logs.map((log, index) => {
            const isWarn = log.level === 'warn';
            const isError = log.level === 'error';
            const eventKind = log.eventKind ?? log.level;
            const eventKindLabel = formatEventKind(eventKind, t);
            const hasEventMessage = log.message && log.message !== eventKind;
            const attempts = log.attempts;
            const shouldShowAttempts =
              attempts !== null &&
              attempts !== undefined &&
              (attempts > 0 || eventKind === 'retried');
            const detailChips = [
              log.durationMs !== null && log.durationMs !== undefined
                ? `${log.durationMs}ms`
                : null,
              log.sourceType ? formatSourceType(log.sourceType, t) : null,
              log.commandStatus ? t('logs.commandStatus', { status: formatCommandStatus(log.commandStatus, t) }) : null,
              log.ackStatus ? t('logs.ackStatus', { status: formatAckStatus(log.ackStatus, t) }) : null,
              log.commandId ? t('logs.commandId', { id: compactId(log.commandId) }) : null,
              shouldShowAttempts
                ? t('logs.attempt', { count: attempts })
                : null,
              log.sourceDetail ? t('logs.source', { source: log.sourceDetail }) : null,
              log.instanceId ? t('logs.instance', { id: compactId(log.instanceId) }) : null,
            ].filter((chip): chip is string => chip !== null);
            let rowClassName = 'border-slate-100 bg-slate-50/70';
            let kindClassName = 'border-slate-200 bg-white text-slate-600';
            if (isWarn) {
              rowClassName = 'border-amber-100 bg-amber-50/60';
              kindClassName = 'border-amber-200 bg-amber-50 text-amber-700';
            }
            if (isError) {
              rowClassName = 'border-rose-100 bg-rose-50/70';
              kindClassName = 'border-rose-200 bg-rose-50 text-rose-700';
            }

            return (
              <div key={log.id ?? `${log.timestamp}:${log.source}:${index}`} className={`rounded-lg border p-3 ${rowClassName}`}>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-mono font-semibold text-slate-400">{log.timestamp}</span>
                  <span className={`rounded border px-1.5 py-0.5 font-bold uppercase ${kindClassName}`}>
                    {eventKindLabel}
                  </span>
                  <span className="font-bold text-slate-500">{log.source}</span>
                  {hasEventMessage ? (
                    <span className="min-w-0 break-words font-semibold text-slate-800">{log.message}</span>
                  ) : null}
                  {detailChips.map((chip) => (
                    <span key={chip} className="rounded border border-slate-200 bg-white px-1.5 py-0.5 font-mono text-[11px] font-semibold text-slate-500">
                      {chip}
                    </span>
                  ))}
                </div>

                {log.traceback ? (
                  <details className="group mt-2">
                    <summary className="flex cursor-pointer select-none items-center gap-1 text-[11px] font-bold text-rose-600">
                      <ChevronDown className="h-3.5 w-3.5 transition-transform group-open:rotate-180" />
                      <span>{t('logs.traceback')}</span>
                    </summary>
                    <pre className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap break-words rounded-md bg-slate-950 p-3 text-[11px] leading-relaxed text-slate-100">
                      {log.traceback}
                    </pre>
                  </details>
                ) : null}
              </div>
            );
          })
        )}
      </div>

      {!isLoading && !error && total > 0 ? (
        <div className="flex flex-col gap-3 rounded-b-xl border-t border-slate-200 bg-slate-50 px-5 py-3 text-xs font-semibold text-slate-500 sm:flex-row sm:items-center sm:justify-between">
          <span>
            {t('task.eventsPagination', {
              start,
              end,
              total,
            })}
          </span>

          <div className="flex items-center gap-1.5">
            <button
              type="button"
              aria-label={t('task.previousPage')}
              onClick={() => onPageChange(Math.max(offset - limit, 0))}
              disabled={!canGoPrevious}
              className="p-1.5 border border-slate-200 rounded-md bg-white text-slate-400 hover:text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:pointer-events-none transition-colors"
            >
              <ChevronLeft className="w-3.5 h-3.5" />
            </button>
            <button
              type="button"
              aria-label={t('task.nextPage')}
              onClick={() => onPageChange(offset + limit)}
              disabled={!canGoNext}
              className="p-1.5 border border-slate-200 rounded-md bg-white text-slate-400 hover:text-slate-700 hover:bg-slate-50 disabled:opacity-50 disabled:pointer-events-none transition-colors"
            >
              <ChevronRight className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

interface TaskEventKindFilterProps {
  selectedKinds: readonly TaskEventHistoryKind[];
  onSelectedKindsChange: (kinds: TaskEventHistoryKind[]) => void;
}

function TaskEventKindFilter({
  selectedKinds,
  onSelectedKindsChange,
}: TaskEventKindFilterProps) {
  const { t } = useI18n();
  const [isOpen, setIsOpen] = useState(false);
  const closeMenu = useCallback(() => setIsOpen(false), []);
  const { menuRef, triggerRef } = useDismissibleMenu({ onClose: closeMenu, open: isOpen });
  const triggerLabel = selectedKinds.length === 0
    ? t('task.eventTypeAll')
    : t('task.eventTypeSelected', { count: selectedKinds.length });

  const toggleKind = (kind: TaskEventHistoryKind) => {
    if (selectedKinds.includes(kind)) {
      onSelectedKindsChange(selectedKinds.filter((selectedKind) => selectedKind !== kind));
      return;
    }
    onSelectedKindsChange([...selectedKinds, kind]);
  };

  const groups: Array<{
    label: string;
    kinds: readonly TaskEventHistoryKind[];
  }> = [
    { label: t('task.runtimeEvents'), kinds: RUNTIME_EVENT_KINDS },
    { label: t('task.controlCommands'), kinds: COMMAND_EVENT_KINDS },
  ];

  return (
    <div className="relative">
      <button
        ref={triggerRef}
        type="button"
        aria-expanded={isOpen}
        aria-haspopup="true"
        onClick={() => setIsOpen((open) => !open)}
        className={`ui-pressable flex h-8 items-center gap-1.5 rounded-md border px-2.5 text-[11px] font-bold transition-colors ${
          selectedKinds.length > 0
            ? 'border-indigo-200 bg-indigo-50 text-indigo-700'
            : 'border-slate-200 bg-white text-slate-600 hover:bg-slate-50'
        }`}
      >
        <ListFilter className="h-3.5 w-3.5" />
        <span>{triggerLabel}</span>
        <ChevronDown className={`h-3.5 w-3.5 transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>

      {isOpen ? (
        <div
          ref={menuRef}
          className="ui-popover-enter absolute left-0 z-40 mt-1 w-64 overflow-hidden rounded-lg border border-slate-200 bg-white text-xs shadow-lg"
        >
          <div className="max-h-80 overflow-y-auto p-2">
            {groups.map((group) => (
              <fieldset key={group.label} className="min-w-0 py-1">
                <legend className="px-2 pb-1 text-[10px] font-bold uppercase text-slate-400">
                  {group.label}
                </legend>
                <div className="space-y-0.5">
                  {group.kinds.map((kind) => (
                    <label
                      key={kind}
                      className="flex min-h-8 cursor-pointer items-center gap-2 rounded-md px-2 text-slate-700 hover:bg-slate-50"
                    >
                      <input
                        type="checkbox"
                        checked={selectedKinds.includes(kind)}
                        onChange={() => toggleKind(kind)}
                        className="h-3.5 w-3.5 rounded border-slate-300 text-indigo-600 focus:ring-indigo-600"
                      />
                      <span className="min-w-0 break-words font-semibold">
                        {formatEventKind(kind, t)}
                      </span>
                    </label>
                  ))}
                </div>
              </fieldset>
            ))}
          </div>
          <div className="border-t border-slate-100 bg-slate-50 p-2">
            <button
              type="button"
              disabled={selectedKinds.length === 0}
              onClick={() => onSelectedKindsChange([])}
              className="ui-pressable w-full rounded-md px-2 py-1.5 text-left font-bold text-slate-600 hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {t('task.clearEventTypeFilter')}
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}

function compactId(value: string): string {
  if (value.length <= 8) return value;
  return value.slice(0, 8);
}

type Translate = (key: MessageKey, values?: Record<string, string | number>) => string;

function formatEventKind(kind: string, t: Translate): string {
  switch (kind) {
    case 'started':
      return t('event.started');
    case 'succeeded':
      return t('event.succeeded');
    case 'failed':
      return t('event.failed');
    case 'retried':
      return t('event.retried');
    case 'dead_lettered':
      return t('event.dead_lettered');
    case 'cancelled':
      return t('event.cancelled');
    case 'pause_task':
      return t('event.pause_task');
    case 'resume_task':
      return t('event.resume_task');
    case 'restart_task':
      return t('event.restart_task');
    case 'discard_dead_letters':
      return t('event.discard_dead_letters');
    case 'replay_dead_letters':
      return t('event.replay_dead_letters');
    case 'run_task_once':
      return t('event.run_task_once');
    default:
      return kind;
  }
}

function formatSourceType(sourceType: NonNullable<LogEntry['sourceType']>, t: Translate): string {
  if (sourceType === 'command') return t('logs.command');
  return t('logs.runtime');
}

function formatCommandStatus(status: string, t: Translate): string {
  switch (status) {
    case 'pending':
      return t('commandStatus.pending');
    case 'dispatched':
      return t('commandStatus.dispatched');
    case 'accepted':
      return t('commandStatus.accepted');
    case 'expired':
      return t('commandStatus.expired');
    case 'rejected':
      return t('commandStatus.rejected');
    case 'succeeded':
      return t('commandStatus.succeeded');
    case 'failed':
      return t('commandStatus.failed');
    case 'timeout':
      return t('commandStatus.timeout');
    case 'cancelled':
      return t('commandStatus.cancelled');
    default:
      return status;
  }
}

function formatAckStatus(status: string, t: Translate): string {
  switch (status) {
    case 'accepted':
      return t('ackStatus.accepted');
    case 'rejected':
      return t('ackStatus.rejected');
    default:
      return status;
  }
}

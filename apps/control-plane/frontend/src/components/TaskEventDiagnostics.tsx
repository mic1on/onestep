import { AlertTriangle, ChevronDown, Terminal } from 'lucide-react';
import type { LogEntry } from '../types';
import { useI18n } from '../i18n';

interface TaskEventDiagnosticsProps {
  logs: LogEntry[];
  isLoading: boolean;
  error: string | null;
}

export default function TaskEventDiagnostics({ logs, isLoading, error }: TaskEventDiagnosticsProps) {
  const { t } = useI18n();

  return (
    <div className="bg-white border border-slate-200 rounded-xl shadow-xs overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-200 bg-slate-50 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Terminal className="h-4 w-4 text-slate-400" />
          <span className="text-xs font-bold text-slate-700">{t('task.recentEvents')}</span>
        </div>
      </div>

      <div className="p-4 space-y-3">
        {isLoading ? (
          <div className="text-xs font-semibold text-slate-500">{t('task.loadingEvents')}</div>
        ) : error ? (
          <div className="flex items-center gap-2 text-xs font-semibold text-rose-600">
            <AlertTriangle className="h-4 w-4" />
            <span>{error}</span>
          </div>
        ) : logs.length === 0 ? (
          <div className="text-xs font-semibold text-slate-500">{t('task.noRecentEvents')}</div>
        ) : (
          logs.map((log, index) => {
            const isWarn = log.level === 'warn';
            const isError = log.level === 'error';
            const eventKind = log.eventKind ?? log.level;
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
              <div key={`${log.timestamp}:${log.source}:${index}`} className={`rounded-lg border p-3 ${rowClassName}`}>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="font-mono font-semibold text-slate-400">{log.timestamp}</span>
                  <span className={`rounded border px-1.5 py-0.5 font-bold uppercase ${kindClassName}`}>
                    {eventKind}
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
    </div>
  );
}

function compactId(value: string): string {
  if (value.length <= 8) return value;
  return value.slice(0, 8);
}

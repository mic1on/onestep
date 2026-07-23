import { useCallback, useState, MouseEvent } from 'react';
import { Play, Square, RotateCcw, Edit3, Eye, MoreVertical, Database, ArrowRight, Layers, HelpCircle, CheckCircle, RefreshCw } from 'lucide-react';
import { Task, type TaskCommandKind } from '../types';
import { useI18n } from '../i18n';
import { getTopologySourceLabel } from './TopologyFlow';
import useDismissibleMenu from './useDismissibleMenu';

interface TasksListProps {
  tasks: Task[];
  onTaskSelect: (task: Task) => void;
  onRestartTask: (taskId: string) => void;
  onToggleTaskStatus: (taskId: string) => void;
  pendingTaskId?: string | null;
}

function taskSupportsCommand(task: Task, command: TaskCommandKind): boolean {
  return task.supportedCommands.includes(command);
}

export default function TasksList({
  tasks,
  onTaskSelect,
  onRestartTask,
  onToggleTaskStatus,
  pendingTaskId,
}: TasksListProps) {
  const { t } = useI18n();
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const closeTaskMenu = useCallback(() => setOpenMenuId(null), []);
  const { menuRef, triggerRef } = useDismissibleMenu({
    onClose: closeTaskMenu,
    open: openMenuId !== null,
  });

  const getTaskStatusLabel = (status: Task['viewStatus']) => {
    if (status === 'running') return t('status.running');
    if (status === 'paused') return t('status.paused');
    if (status === 'idle') return t('status.idle');
    if (status === 'offline') return t('status.offline');
    if (status === 'failed') return t('status.failed');
    return t('status.stopped');
  };

  const toggleMenu = (id: string, e: MouseEvent) => {
    e.stopPropagation();
    triggerRef.current = e.currentTarget as HTMLButtonElement;
    setOpenMenuId(openMenuId === id ? null : id);
  };

  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 sm:gap-4 lg:gap-6">
      {tasks.map((task) => {
        const isRunning = task.viewStatus === 'running';
        const isPaused = task.viewStatus === 'paused';
        const isIdle = task.viewStatus === 'idle';
        const isOffline = task.viewStatus === 'offline';
        const toggleCommand = isPaused ? 'resume_task' : isOffline ? null : 'pause_task';
        const isToggleSupported =
          toggleCommand !== null && taskSupportsCommand(task, toggleCommand);
        const isPauseToggle = toggleCommand === 'pause_task';
        const isRestartSupported = taskSupportsCommand(task, 'restart_task');
        const isPending = pendingTaskId === task.id;
        const isMenuOpen = openMenuId === task.id;
        const sourceLabel = getTopologySourceLabel(task);
        const description = task.description?.trim();

        return (
          <div
            key={task.id}
            onClick={() => onTaskSelect(task)}
            className="ui-pressable relative flex cursor-pointer flex-col justify-between rounded-lg border border-slate-200 bg-white p-5 hover:border-slate-300 hover:shadow-md"
          >
            <div>
              {/* Header section inside card */}
              <div className="flex justify-between items-start mb-4">
                <div className="flex items-center gap-3 min-w-0">
                  <div className="w-10 h-10 rounded-lg bg-slate-50 border border-slate-100 flex items-center justify-center text-indigo-600">
                    <Database className="w-5 h-5" />
                  </div>
                  <div className="min-w-0">
                    <h4 className="font-sans text-md font-bold text-slate-900 group-hover:text-indigo-600 transition-colors truncate">
                      {task.name}
                    </h4>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      <span className="font-mono text-[11px] text-slate-400 bg-slate-50 px-1.5 py-0.5 rounded border border-slate-100">
                        {task.id.slice(0, 6)}
                      </span>
                      <span className="flex items-center gap-1 text-[11px] font-semibold">
                        <span
                          className={`w-1.5 h-1.5 rounded-full ${
                            isRunning
                              ? 'bg-emerald-500'
                              : isPaused
                              ? 'bg-sky-500'
                              : isIdle || isOffline
                              ? 'bg-slate-400'
                              : 'bg-amber-400'
                          }`}
                        />
                        <span
                          className={
                            isRunning
                              ? 'text-emerald-600'
                              : isPaused
                              ? 'text-sky-600'
                              : isIdle
                              ? 'text-slate-600'
                              : isOffline
                              ? 'text-slate-500'
                              : 'text-amber-600'
                          }
                        >
                          {getTaskStatusLabel(task.viewStatus)}
                        </span>
                      </span>
                    </div>
                    {description && (
                      <p
                        className="mt-1.5 line-clamp-2 max-w-md break-words text-xs font-medium leading-5 text-slate-500"
                        title={description}
                      >
                        {description}
                      </p>
                    )}
                  </div>
                </div>

                <div className="relative hidden lg:block">
                  <button
                    onClick={(e) => toggleMenu(task.id, e)}
                    aria-expanded={isMenuOpen}
                    aria-haspopup="menu"
                    aria-label={t('button.moreActions', { name: task.name })}
                    className="ui-pressable rounded-md p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
                    type="button"
                  >
                    <MoreVertical className="w-4 h-4" />
                  </button>

                  {isMenuOpen && (
                    <div
                      ref={menuRef}
                      className="ui-popover-enter absolute right-0 z-30 mt-1 w-44 rounded-lg border border-slate-200 bg-white py-1 text-xs font-medium shadow-lg"
                      role="menu"
                    >
                      <button
                        role="menuitem"
                        onClick={(e) => {
                          e.stopPropagation();
                          onTaskSelect(task);
                          closeTaskMenu();
                        }}
                        className="ui-pressable flex w-full items-center gap-2 px-3 py-2 text-left text-slate-700 hover:bg-slate-50"
                        type="button"
                      >
                        <Eye className="w-3.5 h-3.5 text-slate-400" />
                        <span>{t('button.viewTaskDetails')}</span>
                      </button>
                      <button
                        aria-busy={isPending}
                        role="menuitem"
                        onClick={(e) => {
                          e.stopPropagation();
                          onToggleTaskStatus(task.id);
                          closeTaskMenu();
                          triggerRef.current?.focus();
                        }}
                        disabled={isPending || !isToggleSupported}
                        className="ui-pressable flex w-full items-center gap-2 px-3 py-2 text-left text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                        type="button"
                      >
                        {isPending ? (
                          <>
                            <RefreshCw className="w-3.5 h-3.5 text-slate-400 animate-spin" />
                            <span>{t('button.processing')}</span>
                          </>
                        ) : isPauseToggle ? (
                          <>
                            <Square className="w-3.5 h-3.5 text-slate-400" />
                            <span>{t('button.stopTask')}</span>
                          </>
                        ) : (
                          <>
                            <Play className="w-3.5 h-3.5 text-slate-400" />
                            <span>{isPaused ? t('button.resumeTask') : t('button.stopTask')}</span>
                          </>
                        )}
                      </button>
                      <button
                        aria-busy={isPending}
                        role="menuitem"
                        onClick={(e) => {
                          e.stopPropagation();
                          onRestartTask(task.id);
                          closeTaskMenu();
                          triggerRef.current?.focus();
                        }}
                        disabled={isPending || !isRestartSupported}
                        className="ui-pressable flex w-full items-center gap-2 px-3 py-2 text-left text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-50"
                        type="button"
                      >
                        {isPending ? (
                          <RefreshCw className="w-3.5 h-3.5 text-slate-400 animate-spin" />
                        ) : (
                          <RotateCcw className="w-3.5 h-3.5 text-slate-400" />
                        )}
                        <span>
                          {isPending ? t('button.processing') : t('button.restartTask')}
                        </span>
                      </button>
                    </div>
                  )}
                </div>
              </div>

              {/* Pipeline details */}
              <div className="bg-slate-50 border border-slate-100 rounded-lg p-3 mb-4">
                <span className="text-[10px] uppercase font-bold tracking-wider text-slate-400 block mb-1.5">
                  {t('tasks.pipeline')}
                </span>
                <div className="flex items-center gap-2.5">
                  <div className="flex flex-col">
                    <span className="text-xs font-bold text-slate-700">{task.pipelineSource}</span>
                    <span className="font-mono text-[10px] text-slate-400">{sourceLabel}</span>
                  </div>
                  <ArrowRight className="w-4 h-4 text-slate-300 shrink-0" />
                  <div className="flex flex-col">
                    <span className="text-xs font-bold text-slate-700">{task.pipelineSink}</span>
                    <span className="font-mono text-[10px] text-slate-400">{task.pipelineSinkLabel}</span>
                  </div>
                </div>
              </div>
            </div>

            {/* Bottom meta stats */}
            <div className="grid grid-cols-2 gap-4 border-t border-slate-100 pt-3">
              <div>
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">
                  {t('tasks.concurrency')}
                </span>
                <span className="text-xs font-semibold text-slate-700 flex items-center gap-1 mt-0.5">
                  <Layers className="w-3.5 h-3.5 text-slate-400" />
                  {t('common.threads', { count: task.concurrency })}
                </span>
              </div>
              <div>
                <span className="text-[10px] font-bold text-slate-400 uppercase tracking-wider block">
                  {t('tasks.retryPolicy')}
                </span>
                <span className="text-xs font-semibold text-slate-700 flex items-center gap-1 mt-0.5">
                  <CheckCircle className="w-3.5 h-3.5 text-slate-400" />
                  {t('common.attempts', { count: task.retryAttempts })}
                </span>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

import { useEffect } from 'react';
import { X } from 'lucide-react';

export type ToastType = 'success' | 'info' | 'warn';

export interface ToastMessage {
  id: string;
  message: string;
  type: ToastType;
}

interface ToastViewportProps {
  dismissLabel: string;
  onDismiss: (id: string) => void;
  toasts: ToastMessage[];
}

interface ToastItemProps {
  dismissLabel: string;
  onDismiss: (id: string) => void;
  toast: ToastMessage;
}

function ToastItem({ dismissLabel, onDismiss, toast }: ToastItemProps) {
  useEffect(() => {
    const timeoutId = window.setTimeout(() => onDismiss(toast.id), 4000);
    return () => window.clearTimeout(timeoutId);
  }, [onDismiss, toast.id]);

  const palette =
    toast.type === 'info'
      ? 'border-slate-700/60 bg-[#1a1c24] text-white'
      : toast.type === 'warn'
        ? 'border-rose-200 bg-rose-50 text-rose-900'
        : 'border-slate-200 bg-white text-slate-800';
  const indicator =
    toast.type === 'warn' ? 'bg-rose-500' : toast.type === 'success' ? 'bg-emerald-500' : 'bg-indigo-600';

  return (
    <div
      aria-atomic="true"
      className={`ui-toast-enter flex items-center gap-3 rounded-lg border p-3.5 text-xs font-medium shadow-lg ${palette}`}
      role={toast.type === 'warn' ? 'alert' : 'status'}
    >
      <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${indicator}`} />
      <span className="min-w-0 flex-1 break-words font-semibold leading-relaxed">{toast.message}</span>
      <button
        aria-label={dismissLabel}
        className="ui-pressable grid h-7 w-7 shrink-0 place-items-center rounded-md text-current opacity-55 hover:bg-black/5 hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500"
        onClick={() => onDismiss(toast.id)}
        type="button"
      >
        <X aria-hidden="true" className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

export default function ToastViewport({ dismissLabel, onDismiss, toasts }: ToastViewportProps) {
  return (
    <div className="fixed inset-x-4 bottom-4 z-50 ml-auto max-w-sm space-y-2.5 sm:inset-x-auto sm:bottom-6 sm:right-6 sm:w-full">
      {toasts.map((toast) => (
        <ToastItem dismissLabel={dismissLabel} key={toast.id} onDismiss={onDismiss} toast={toast} />
      ))}
    </div>
  );
}

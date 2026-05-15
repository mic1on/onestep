import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

export type ToastTone = "success" | "error" | "warning" | "info";

type ToastItem = {
  id: number;
  tone: ToastTone;
  message: string;
};

type ToastContextValue = {
  pushToast: (input: { tone: ToastTone; message: string }) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

const TOAST_LIFETIME_MS = 3200;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const timersRef = useRef(new Map<number, number>());
  const idRef = useRef(0);

  const dismissToast = useCallback((id: number) => {
    setToasts((current) => current.filter((toast) => toast.id !== id));
    const timer = timersRef.current.get(id);
    if (timer !== undefined) {
      window.clearTimeout(timer);
      timersRef.current.delete(id);
    }
  }, []);

  const pushToast = useCallback(
    ({ tone, message }: { tone: ToastTone; message: string }) => {
      const id = ++idRef.current;
      setToasts((current) => [...current, { id, tone, message }]);
      const timer = window.setTimeout(() => {
        dismissToast(id);
      }, TOAST_LIFETIME_MS);
      timersRef.current.set(id, timer);
    },
    [dismissToast],
  );

  useEffect(() => {
    return () => {
      for (const timer of timersRef.current.values()) {
        window.clearTimeout(timer);
      }
      timersRef.current.clear();
    };
  }, []);

  const value = useMemo(() => ({ pushToast }), [pushToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div aria-live="polite" className="toast-stack" role="status">
        {toasts.map((toast) => (
          <div className={`toast-card is-${toast.tone}`} key={toast.id}>
            <span className="toast-card-dot" aria-hidden="true" />
            <p>{toast.message}</p>
            <button
              aria-label="Dismiss notification"
              className="toast-card-close"
              onClick={() => dismissToast(toast.id)}
              type="button"
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast() {
  const context = useContext(ToastContext);
  if (!context) {
    throw new Error("useToast must be used within ToastProvider");
  }
  return context;
}

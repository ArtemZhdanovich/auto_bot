/** Тосты (UI/UX ТЗ §2.10): ToastProvider + useToast. */

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";

interface Toast {
  id: number;
  text: string;
  error: boolean;
}

interface ToastContextValue {
  show: (text: string, opts?: { error?: boolean; durationMs?: number }) => void;
}

const ToastContext = createContext<ToastContextValue>({ show: () => undefined });

export function useToast(): ToastContextValue {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(1);

  const show = useCallback(
    (text: string, opts?: { error?: boolean; durationMs?: number }) => {
      const id = nextId.current++;
      setToasts((prev) => [...prev.slice(-2), { id, text, error: opts?.error ?? false }]);
      window.setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, opts?.durationMs ?? 2500);
    },
    [],
  );

  return (
    <ToastContext.Provider value={{ show }}>
      {children}
      <div className="toast-host">
        {toasts.map((t) => (
          <div key={t.id} className={t.error ? "toast toast--error" : "toast"}>
            {t.text}
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

/**
 * Партнёрские тосты с action-кнопкой (02 §7.8, 04 §5.1/P7/X2).
 * Отдельная от мини-аппа реализация: глобальный ui/Toast не поддерживает
 * действие в тосте («Открыть», «Обновить»), а менять его нельзя (не наш файл).
 */

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
  type ReactNode,
} from "react";

export interface PToastAction {
  label: string;
  onClick: () => void;
}

interface PToastOptions {
  error?: boolean;
  /** Кнопка-действие внутри тоста (02 §7.8). */
  action?: PToastAction;
  durationMs?: number;
}

interface PToastItem {
  id: number;
  text: string;
  error: boolean;
  action?: PToastAction;
}

interface PToastContextValue {
  show: (text: string, opts?: PToastOptions) => void;
}

const PToastContext = createContext<PToastContextValue>({ show: () => undefined });

export function usePToast(): PToastContextValue {
  return useContext(PToastContext);
}

export function PToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<PToastItem[]>([]);
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const show = useCallback(
    (text: string, opts?: PToastOptions) => {
      const id = nextId.current++;
      const item: PToastItem = {
        id,
        text,
        error: opts?.error ?? false,
        action: opts?.action,
      };
      // Стек ≤3 (02 §7.8): старые сворачиваются
      setToasts((prev) => [...prev.slice(-2), item]);
      window.setTimeout(() => dismiss(id), opts?.durationMs ?? (item.error ? 5000 : 3200));
    },
    [dismiss],
  );

  return (
    <PToastContext.Provider value={{ show }}>
      {children}
      <div className="pa-toast-host" role="status" aria-live="polite">
        {toasts.map((t) => (
          <div key={t.id} className={t.error ? "pa-toast pa-toast--error" : "pa-toast"}>
            <span className="pa-toast__text">{t.text}</span>
            {t.action && (
              <button
                type="button"
                className="pa-toast__action"
                onClick={() => {
                  t.action?.onClick();
                  dismiss(t.id);
                }}
              >
                {t.action.label}
              </button>
            )}
          </div>
        ))}
      </div>
    </PToastContext.Provider>
  );
}

/**
 * Модалка «Нужен вход» для гостей: персональные разделы (избранное,
 * уведомления) доступны после входа/регистрации. Использование:
 *   const prompt = useLoginPrompt();
 *   prompt.show("Избранное доступно после входа");
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { useNavigate } from "react-router-dom";

interface LoginPromptContextValue {
  show: (reason?: string) => void;
}

const LoginPromptContext = createContext<LoginPromptContextValue>({
  show: () => undefined,
});

export function useLoginPrompt(): LoginPromptContextValue {
  return useContext(LoginPromptContext);
}

const DEFAULT_REASON =
  "Войдите, чтобы сохранять машины в избранное и получать уведомления в Telegram.";

export function LoginPromptProvider({ children }: { children: ReactNode }) {
  const [reason, setReason] = useState<string | null>(null);
  const navigate = useNavigate();

  const show = useCallback((r?: string) => setReason(r ?? DEFAULT_REASON), []);
  const close = useCallback(() => setReason(null), []);

  // Esc закрывает модалку (03-content-a11y §5.3 — как шит ленты и лайтбокс)
  useEffect(() => {
    if (reason === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setReason(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [reason]);
  const goLogin = useCallback(() => {
    setReason(null);
    navigate("/login");
  }, [navigate]);

  return (
    <LoginPromptContext.Provider value={{ show }}>
      {children}
      {reason !== null && (
        <div className="modal" onClick={close} role="dialog" aria-modal="true">
          <div className="modal__card" onClick={(e) => e.stopPropagation()}>
            <div className="modal__emoji">🔒</div>
            <div className="modal__title">Нужен вход</div>
            <div className="modal__text">{reason}</div>
            <div className="modal__actions">
              <button className="modal__btn modal__btn--primary" onClick={goLogin}>
                Войти
              </button>
              <button className="modal__btn" onClick={close}>
                Позже
              </button>
            </div>
          </div>
        </div>
      )}
    </LoginPromptContext.Provider>
  );
}

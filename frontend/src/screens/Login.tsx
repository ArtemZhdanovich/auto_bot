/** Экран «Сайт: вход/регистрация» (вне Telegram). Поиск — только после регистрации. */

import { useState } from "react";

import { webLogin, webRegister } from "../api/auth";

interface Props {
  onAuthed: () => void;
}

export function LoginScreen({ onAuthed }: Props) {
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [firstName, setFirstName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setError(null);
    setBusy(true);
    try {
      if (mode === "register") {
        await webRegister(email, password, firstName || undefined);
      } else {
        await webLogin(email, password);
      }
      onAuthed();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Ошибка сети");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="screen auth">
      <div className="auth__hero">
        <div className="auth__hero-icon">🔐</div>
        <h1 className="auth__title">{mode === "login" ? "Вход" : "Регистрация"}</h1>
        <p className="auth__sub">
          Поиск по объявлениям, слежение и AI-подбор доступны после входа.
          Уведомления приходят только в Telegram.
        </p>
      </div>

      <div className="auth__tabs" role="tablist">
        <button
          className={mode === "login" ? "auth__tab auth__tab--active" : "auth__tab"}
          onClick={() => setMode("login")}
        >
          Вход
        </button>
        <button
          className={mode === "register" ? "auth__tab auth__tab--active" : "auth__tab"}
          onClick={() => setMode("register")}
        >
          Регистрация
        </button>
      </div>

      <form
        className="panel auth__form"
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        {mode === "register" && (
          <label className="calc-field">
            <span>Имя (необязательно)</span>
            <input value={firstName} onChange={(e) => setFirstName(e.target.value)} placeholder="Иван" />
          </label>
        )}
        <label className="calc-field">
          <span>Email</span>
          <input
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="you@mail.com"
            type="email"
            autoComplete="email"
          />
        </label>
        <label className="calc-field">
          <span>Пароль</span>
          <input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={mode === "register" ? "Минимум 8 символов" : "Пароль"}
            type="password"
            autoComplete={mode === "register" ? "new-password" : "current-password"}
          />
        </label>
        {error && <div className="form-error">{error}</div>}
        <button
          className="btn btn--primary btn--block"
          disabled={busy || !email || password.length < (mode === "register" ? 8 : 1)}
        >
          {busy ? "Секунду…" : mode === "register" ? "Зарегистрироваться" : "Войти"}
        </button>
        <p className="auth__hint">
          Уведомления о новых вариантах отправляются только в Telegram — привяжите
          бота после входа.
        </p>
      </form>
    </div>
  );
}

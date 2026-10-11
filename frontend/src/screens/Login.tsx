/** Экран «Сайт: вход/регистрация» (вне Telegram). Поиск — только после регистрации. */

import { useState } from "react";
import { Button, Input, Placeholder, Section } from "@telegram-apps/telegram-ui";

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
    <div style={{ padding: 16 }}>
      <Placeholder
        header={mode === "login" ? "Вход" : "Регистрация"}
        description="Поиск по объявлениям, фильтры и AI-подбор доступны после входа. Уведомления приходят только в Telegram."
      />
      <Section>
        <div style={{ display: "flex", gap: 8, padding: 12 }}>
          <Button
            mode={mode === "login" ? "filled" : "plain"}
            stretched
            onClick={() => setMode("login")}
          >
            Вход
          </Button>
          <Button
            mode={mode === "register" ? "filled" : "plain"}
            stretched
            onClick={() => setMode("register")}
          >
            Регистрация
          </Button>
        </div>
        <div style={{ display: "grid", gap: 8, padding: 12 }}>
          {mode === "register" && (
            <Input value={firstName} onChange={(e) => setFirstName(e.target.value)} placeholder="Имя (необязательно)" />
          )}
          <Input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" type="email" />
          <Input
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={mode === "register" ? "Пароль (мин. 8 символов)" : "Пароль"}
            type="password"
          />
          {error && <div style={{ color: "var(--tg-theme-destructive-text-color, #e53935)", fontSize: 13 }}>{error}</div>}
          <Button size="l" stretched disabled={busy || !email || password.length < (mode === "register" ? 8 : 1)} onClick={submit}>
            {mode === "register" ? "Зарегистрироваться" : "Войти"}
          </Button>
          <div style={{ fontSize: 12, opacity: 0.7 }}>
            Уведомления о новых вариантах отправляются только в Telegram — привяжите бота после входа.
          </div>
        </div>
      </Section>
    </div>
  );
}

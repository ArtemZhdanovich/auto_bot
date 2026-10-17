/** Экран «AI-поиск»: чат с агентом как в боте (ТЗ: ИИ-поиск прямо в аппке). */

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button } from "@telegram-apps/telegram-ui";

import { aiFinalize, aiMessage, aiReset, type AiReply } from "../api/ai";

interface Bubble {
  role: "user" | "bot";
  text: string;
  options?: string[];
  finalize?: boolean;
}

export function AiSearchScreen() {
  const [bubbles, setBubbles] = useState<Bubble[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [lastReply, setLastReply] = useState<AiReply | null>(null);
  const navigate = useNavigate();
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [bubbles]);

  const send = async (text: string) => {
    const clean = text.trim();
    if (!clean || busy) return;
    setBusy(true);
    setInput("");
    setBubbles((prev) => [...prev, { role: "user", text: clean }]);
    try {
      const reply = await aiMessage(clean);
      setLastReply(reply);
      setBubbles((prev) => [
        ...prev,
        { role: "bot", text: reply.text, options: reply.options, finalize: reply.finalize_ready },
      ]);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Ошибка сети";
      setBubbles((prev) => [...prev, { role: "bot", text: `⚠️ ${msg}` }]);
    } finally {
      setBusy(false);
    }
  };

  const finalize = async () => {
    if (busy) return;
    setBusy(true);
    try {
      const res = await aiFinalize((lastReply?.criteria as Record<string, unknown>) ?? undefined);
      setBubbles((prev) => [
        ...prev,
        {
          role: "bot",
          text: `🔎 Поиск запущен! Источники: ${res.sources?.join(", ") || "нет активных"}. Новые варианты пришлём в Telegram.`,
        },
      ]);
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : "Ошибка сети";
      setBubbles((prev) => [...prev, { role: "bot", text: `⚠️ ${msg}` }]);
    } finally {
      setBusy(false);
    }
  };

  const reset = async () => {
    await aiReset().catch(() => undefined);
    setBubbles([]);
    setLastReply(null);
  };

  const last = bubbles[bubbles.length - 1];

  return (
    <div className="ai-chat">
      {bubbles.length === 0 && (
        <div className="ai-chat__intro">
          <div className="ai-chat__intro-icon">🤖</div>
          <div className="ai-chat__intro-title">AI-подбор автомобиля</div>
          <div className="ai-chat__intro-text">
            Опишите словами, что ищете: «Hyundai Palisade 2022–2024, дизель,
            до 45к $» — агент задаст уточняющие вопросы и запустит поиск по
            всем источникам.
          </div>
        </div>
      )}

      <div className="ai-chat__history">
        {bubbles.map((b, i) => (
          <div
            key={i}
            className={b.role === "user" ? "ai-msg ai-msg--user" : "ai-msg ai-msg--bot"}
          >
            <div className="ai-msg__text">{b.text}</div>
          </div>
        ))}
        {busy && (
          <div className="ai-msg ai-msg--bot">
            <div className="ai-msg__text ai-msg__text--typing">
              <span /><span /><span />
            </div>
          </div>
        )}
      </div>

      {last?.options && last.options.length > 0 && (
        <div className="ai-chat__options">
          {last.options.map((o) => (
            <button key={o} className="chip" onClick={() => send(o)}>
              {o}
            </button>
          ))}
        </div>
      )}

      {last?.finalize && (
        <div className="ai-chat__finalize">
          <Button size="l" stretched loading={busy} onClick={finalize}>
            🔎 Запустить поиск
          </Button>
        </div>
      )}

      <div ref={bottomRef} />

      <div className="ai-chat__composer">
        <textarea
          className="ai-chat__input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
          placeholder="Например: Kia Carnival, дизель, до 50к $"
          rows={2}
        />
        <div className="ai-chat__composer-actions">
          <Button size="s" stretched loading={busy} disabled={!input.trim()} onClick={() => void send(input)}>
            Отправить
          </Button>
          <Button mode="outline" size="s" onClick={() => void reset()}>
            Сброс
          </Button>
        </div>
        <Button mode="plain" size="s" onClick={() => navigate("/filters")}>
          Настроить слежение вручную →
        </Button>
      </div>
    </div>
  );
}

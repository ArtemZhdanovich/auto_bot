/** Экран «AI-поиск»: чат с агентом как в боте (ТЗ: ИИ-поиск прямо в аппке). */

import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, List, Placeholder, Section, Spinner, Textarea } from "@telegram-apps/telegram-ui";

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
    <List>
      {bubbles.length === 0 && (
        <Placeholder
          header="AI-подбор автомобиля"
          description="Опишите словами, что ищете: «Hyundai Palisade 2022–2024, дизель, до 45к $» — агент задаст уточняющие вопросы и запустит поиск."
        />
      )}

      {bubbles.map((b, i) => (
        <Section key={i} style={{ marginBottom: 8 }}>
          <div style={{ padding: "12px 16px", whiteSpace: "pre-wrap" }}>
            <b>{b.role === "user" ? "Вы" : "Агент"}:</b> {b.text}
          </div>
        </Section>
      ))}

      {busy && <Spinner size="m" />}

      {last?.options && last.options.length > 0 && (
        <Section header="Варианты ответа">
          {last.options.map((o) => (
            <Button key={o} mode="plain" size="s" stretched before={undefined} onClick={() => send(o)}>
              {o}
            </Button>
          ))}
        </Section>
      )}

      {last?.finalize && (
        <Section>
          <Button size="l" stretched onClick={finalize}>
            🔎 Запустить поиск
          </Button>
        </Section>
      )}

      <div ref={bottomRef} />

      <Section header="Сообщение агенту">
        <Textarea value={input} onChange={(e) => setInput(e.target.value)} placeholder="Например: Kia Carnival, дизель, до 50к $" />
        <div style={{ display: "flex", gap: 8, padding: 12 }}>
          <Button size="l" stretched disabled={busy || !input.trim()} onClick={() => send(input)}>
            Отправить
          </Button>
          <Button mode="outline" size="l" onClick={reset}>
            Сброс
          </Button>
        </div>
      </Section>

      <Section>
        <Button mode="plain" stretched onClick={() => navigate("/filters")}>
          Настроить фильтры вручную →
        </Button>
      </Section>
    </List>
  );
}

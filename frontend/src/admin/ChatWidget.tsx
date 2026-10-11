/**
 * AI-помощник админа (UI/UX ТЗ §3.2, фаза 4).
 * Плавающий виджет: ход через POST /api/admin/assistant/chat —
 * LLM-агент с tools (лиды/партнёры/источники) на backend.
 */

import { useEffect, useRef, useState } from "react";
import { Bot, Send, X } from "lucide-react";

import { AdminApiError, aapi } from "./api";

interface Turn {
  role: "user" | "assistant";
  content: string;
}

const GREETING: Turn = {
  role: "assistant",
  content:
    "Привет! Я могу показать статистику лидов, список партнёров и здоровье источников. Спросите, например: «Сколько новых лидов?»",
};

export function ChatWidget() {
  const [open, setOpen] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([GREETING]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [turns, open]);

  const send = async () => {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    const history = turns.filter((t) => t !== GREETING);
    setTurns((prev) => [...prev, { role: "user", content: text }]);
    setBusy(true);
    try {
      const res = await aapi<{ reply: string }>("/assistant/chat", {
        method: "POST",
        body: JSON.stringify({ message: text, history }),
      });
      setTurns((prev) => [...prev, { role: "assistant", content: res.reply }]);
    } catch (e) {
      const msg =
        e instanceof AdminApiError && e.status === 503
          ? "LLM недоступен — проверьте GLM_API_KEY на backend."
          : (e as Error).message;
      setTurns((prev) => [...prev, { role: "assistant", content: `⚠️ ${msg}` }]);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <button
        onClick={() => setOpen((v) => !v)}
        style={{
          position: "fixed",
          right: 24,
          bottom: 24,
          width: 52,
          height: 52,
          borderRadius: "var(--radius-full)",
          border: "none",
          background: "var(--accent)",
          color: "var(--accent-text)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          boxShadow: "var(--shadow-lg)",
          cursor: "pointer",
          zIndex: 90,
        }}
        aria-label="AI-помощник"
      >
        {open ? <X size={22} /> : <Bot size={22} />}
      </button>

      {open && (
        <div
          className="panel"
          style={{
            position: "fixed",
            right: 24,
            bottom: 88,
            width: "min(360px, 92vw)",
            height: 440,
            display: "flex",
            flexDirection: "column",
            zIndex: 90,
            marginBottom: 0,
          }}
        >
          <h3 style={{ margin: 0 }}>AI-помощник</h3>
          <div
            ref={bodyRef}
            style={{
              flex: 1,
              overflowY: "auto",
              display: "flex",
              flexDirection: "column",
              gap: 8,
              padding: "12px 0",
            }}
          >
            {turns.map((t, i) => (
              <div
                key={i}
                style={{
                  alignSelf: t.role === "user" ? "flex-end" : "flex-start",
                  maxWidth: "85%",
                  padding: "8px 12px",
                  borderRadius: 12,
                  fontSize: 14,
                  whiteSpace: "pre-wrap",
                  background:
                    t.role === "user" ? "var(--accent)" : "var(--bg-secondary)",
                  color: t.role === "user" ? "var(--accent-text)" : "var(--text-primary)",
                }}
              >
                {t.content}
              </div>
            ))}
            {busy && (
              <div style={{ color: "var(--text-secondary)", fontSize: 13 }}>
                Думаю…
              </div>
            )}
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="input"
              placeholder="Спросите о лидах, партнёрах, источниках…"
              value={input}
              disabled={busy}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") void send();
              }}
            />
            <button
              className="btn btn--primary"
              disabled={busy || !input.trim()}
              onClick={() => void send()}
              aria-label="Отправить"
            >
              <Send size={16} />
            </button>
          </div>
        </div>
      )}
    </>
  );
}

/**
 * AI-помощник админа — скелет (UI/UX ТЗ §3.2, фаза 4).
 * LLM-агент и tools подключаются позже; сейчас — UI-каркас.
 */

import { useState } from "react";
import { Bot, X } from "lucide-react";

export function ChatWidget() {
  const [open, setOpen] = useState(false);

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
            height: 420,
            display: "flex",
            flexDirection: "column",
            zIndex: 90,
            marginBottom: 0,
          }}
        >
          <h3 style={{ margin: 0 }}>AI-помощник</h3>
          <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
            <p style={{ color: "var(--text-secondary)", fontSize: 14, textAlign: "center" }}>
              Помощник в разработке: сможет отвечать на вопросы по лидам,
              источникам и партнёрам.
            </p>
          </div>
          <input className="input" placeholder="Скоро…" disabled />
        </div>
      )}
    </>
  );
}

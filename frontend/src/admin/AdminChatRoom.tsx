/**
 * Комната оператора поддержки (admin, 05-chats-marketplace.md §2/§6):
 * минимальное зеркало ядра чатов без сокета — лента через GET
 * /api/admin/chats/{id}/messages (поллинг 10с по after_id), композер
 * через POST (sender_type='operator' → бэк сам ставит bot_paused).
 *
 * Свои (operator) — справа; юзер («Клиент») и бот — слева/по центру;
 * тело — markdown (lib/markdown, тот же рендер что в мини-аппе).
 * «📄 Выжимка» (12-ai-trust §3): POST …/summary → раскрывающийся блок
 * с LLM-выжимкой диалога; повторный клик обновляет (кэш бэка 1ч).
 * Стили — admin/admin-chats.css (классы ac-*, не зависят от chats.css).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { AdminChatMessage, AdminChatSummary, AdminSupportThread } from "./api";
import {
  AdminApiError,
  fetchAdminChatSummary,
  fetchAdminMessages,
  sendAdminMessage,
} from "./api";
import { Markdown } from "../lib/markdown";
import { formatMsgTime } from "../components/chat/Message";

/** Интервал поллинга комнаты (сек): без сокета админке хватает 10с. */
const ROOM_POLL_SEC = 10;

/** Подписи отправителей (свои = operator — без подписи). */
const SENDER_LABEL: Record<AdminChatMessage["sender_type"], string | null> = {
  user: "Клиент",
  operator: null,
  partner: "Продавец",
  bot: "Бот",
  system: null,
};

function AdminMessage({
  message,
  showSender,
}: {
  message: AdminChatMessage;
  showSender: boolean;
}): JSX.Element {
  const own = message.sender_type === "operator";
  const centered =
    message.sender_type === "system" || message.sender_type === "bot";

  if (message.sender_type === "system") {
    return (
      <div className="ac-msg ac-msg--system" data-id={message.id}>
        <span>{message.body}</span>
        <span className="ac-msg__time">{formatMsgTime(message.created_at)}</span>
      </div>
    );
  }

  const label = SENDER_LABEL[message.sender_type];
  const cls = [
    "ac-msg",
    own ? "ac-msg--own" : "ac-msg--other",
    centered ? "ac-msg--centered" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} data-id={message.id}>
      {showSender && label && !centered && (
        <div className="ac-msg__sender">{label}</div>
      )}
      <div className="ac-msg__bubble">
        <Markdown body={message.body} />
        <span className="ac-msg__time">{formatMsgTime(message.created_at)}</span>
      </div>
    </div>
  );
}

export interface AdminChatRoomProps {
  thread: AdminSupportThread;
  onBack: () => void;
  /** 401 — internal-токен отозван: на экран входа. */
  onUnauthorized?: () => void;
}

export function AdminChatRoom({ thread, onBack, onUnauthorized }: AdminChatRoomProps) {
  const [messages, setMessages] = useState<AdminChatMessage[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  /** «📄 Выжимка» (12 §3): блок под шапкой; повторный клик — обновление. */
  const [summary, setSummary] = useState<AdminChatSummary | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [summaryBusy, setSummaryBusy] = useState(false);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const lastIdRef = useRef(0);

  const failText = useCallback(
    (e: unknown): string => {
      if (e instanceof AdminApiError && e.status === 401) {
        onUnauthorized?.();
        return "Токен недействителен";
      }
      return (e as Error).message || "Сервис недоступен";
    },
    [onUnauthorized],
  );

  const appendMessages = useCallback((items: AdminChatMessage[]) => {
    setMessages((prev) => {
      const seen = new Set(prev.map((m) => m.id));
      const fresh = items.filter((m) => !seen.has(m.id));
      if (fresh.length) lastIdRef.current = Math.max(lastIdRef.current, ...fresh.map((m) => m.id));
      return fresh.length ? [...prev, ...fresh] : prev;
    });
  }, []);

  // Первая загрузка: последние 50 сообщений
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    setMessages([]);
    lastIdRef.current = 0;
    // Выжимка относится к прошлому треду — сбрасываем (12 §3)
    setSummary(null);
    setSummaryOpen(false);
    setSummaryError(null);
    fetchAdminMessages(thread.id)
      .then((page) => {
        if (!alive) return;
        const items = [...page.items].sort((a, b) => a.id - b.id);
        setMessages(items);
        lastIdRef.current = items.length ? items[items.length - 1].id : 0;
      })
      .catch((e: unknown) => alive && setError(failText(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [thread.id, failText]);

  // Поллинг 10с: только хвост после последнего известного id (after_id)
  useEffect(() => {
    const timer = window.setInterval(() => {
      fetchAdminMessages(thread.id, { afterId: lastIdRef.current })
        .then((page) => appendMessages(page.items))
        .catch(() => undefined); // поллинг не роняет комнату
    }, ROOM_POLL_SEC * 1000);
    return () => window.clearInterval(timer);
  }, [thread.id, appendMessages]);

  // Новые сообщения — держим ленту прижатой к низу
  useEffect(() => {
    const el = scrollRef.current;
    if (el && el.scrollTo) el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
  }, [messages.length]);

  /** Отправка ответа оператора: REST; ответ бэка падает в ленту. */
  const send = async () => {
    const body = input.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      const m = await sendAdminMessage(thread.id, body);
      appendMessages([m]);
      setInput("");
    } catch (e) {
      setError(failText(e));
    } finally {
      setSending(false);
    }
  };

  /** «📄 Выжимка» (12 §3): POST …/summary → раскрывающийся блок под шапкой.
   *  Повторный клик обновляет выжимку (бэк кэширует 1ч по last_message_id). */
  const showSummary = async () => {
    if (summaryBusy) return;
    setSummaryOpen(true);
    setSummaryBusy(true);
    setSummaryError(null);
    try {
      setSummary(await fetchAdminChatSummary(thread.id));
    } catch (e) {
      setSummaryError(failText(e));
    } finally {
      setSummaryBusy(false);
    }
  };

  const closed = thread.status === "closed";

  return (
    <div className="ac-room" data-testid="admin-chat-room">
      <header className="ac-room__head">
        <button type="button" className="btn" aria-label="К списку тредов" onClick={onBack}>
          ‹
        </button>
        <div className="ac-room__titles">
          <h3 className="ac-room__title">
            {thread.user_name || `Клиент #${thread.user_id}`}
          </h3>
          <span className="ac-room__sub">
            Поддержка{thread.bot_paused ? " · 🔕 бот на паузе" : ""}
            {closed ? " · закрыт" : ""}
          </span>
        </div>
        <button
          type="button"
          className="ac-room__summary-btn"
          aria-expanded={summaryOpen}
          disabled={summaryBusy}
          onClick={() => void showSummary()}
        >
          📄 Выжимка
        </button>
      </header>

      {/* Раскрывающийся блок выжимки (12 §3): текст + «по состоянию на …» */}
      {summaryOpen && (
        <div className="ac-summary" data-testid="admin-chat-summary">
          {summaryBusy ? (
            <div className="ac-summary__loading">Собираем выжимку…</div>
          ) : summaryError ? (
            <div className="ac-summary__loading ac-summary__loading--error">{summaryError}</div>
          ) : summary ? (
            <>
              <div className="ac-summary__head">
                <span className="ac-summary__title">Выжимка диалога</span>
                <span className="ac-summary__as-of">
                  по состоянию на сообщение №{summary.as_of_message_id}
                </span>
              </div>
              <p className="ac-summary__text">{summary.summary}</p>
            </>
          ) : null}
        </div>
      )}

      <div className="ac-room__feed" ref={scrollRef} role="log" aria-label="Сообщения треда">
        {loading && <div className="ac-room__loading">Загружаем сообщения…</div>}
        {error && <div className="ac-room__loading ac-room__loading--error">{error}</div>}
        {!loading &&
          messages.map((m, i) => (
            <AdminMessage key={m.id} message={m} showSender={messages[i - 1]?.sender_type !== m.sender_type} />
          ))}
      </div>

      <div className="ac-composer">
        <textarea
          className="ac-composer__input"
          aria-label="Ответ оператора"
          placeholder={closed ? "Чат закрыт" : "Ответ оператора… (Enter — отправить)"}
          rows={2}
          maxLength={4000}
          value={input}
          disabled={closed}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <button
          type="button"
          className="btn btn--primary"
          aria-label="Отправить"
          disabled={!input.trim() || sending || closed}
          onClick={() => void send()}
        >
          ➤
        </button>
      </div>
    </div>
  );
}

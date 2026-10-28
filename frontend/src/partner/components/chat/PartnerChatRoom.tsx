/**
 * Чат партнёрского кабинета (F2, 05-chats-marketplace.md §7): обёртка ядра
 * чатов F1 — лента, композер и стили те же (components/chat + chats.css
 * грузится глобально), но зеркало ориентировано на продавца:
 * - «свои» = сообщения партнёра (sender_type='partner') — справа, с ✓/✓✓
 *   по read-маркеру юзера; юзер («Клиент») и оператор — слева с подписью;
 * - транспорт — createChatTransport с партнёрским ws-токеном
 *   (GET /api/partner/chats/ws-token, sub=partner:{id}, канал thread:{id})
 *   и поллинг-фоллбэком через papi (X-API-Key);
 * - отправка — только REST (POST /api/partner/chats/{id}/messages);
 * - предмет треда открывает карточку лота мини-аппа в новой вкладке
 *   (кабинет — отдельный роутер, внутренний navigate невозможен);
 * - group-комнаты (08-group-governance §7, партнёрский минимум): в шапке
 *   mute-колокольчик (POST …/mute|unmute) и «Выйти» (POST …/leave → к списку).
 * - «📄 Выжимка» (12-ai-trust §3): POST …/summary → раскрывающийся блок
 *   с LLM-выжимкой диалога; повторный клик обновляет (кэш бэка 1ч).
 * Файл ChatRoom.tsx (ядро, user-контур) не меняется.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { ChatEvent, ChatMessage, ChatTransport } from "../../../lib/chatSocket";
import { createChatTransport, threadChannel } from "../../../lib/chatSocket";
import { THREAD_TYPE_LABEL } from "../../../lib/chatApi";
import { Markdown } from "../../../lib/markdown";
import { extractSoloLink, LinkPreviewCard } from "../../../lib/linkPreview";
import { formatMsgTime } from "../../../components/chat/Message";
import { usePToast } from "../toast";
import {
  PartnerApiError,
  fetchPartnerChatSummary,
  fetchPartnerLinkPreview,
  fetchPartnerMessages,
  fetchPartnerWsToken,
  markPartnerRead,
  papi,
  partnerFail,
  sendPartnerMessage,
  type PartnerChatSummary,
  type PartnerThread,
} from "../../api";

/** Максимум строк авто-растущей textarea (~22px строка + паддинги) — как в ядре. */
const COMPOSER_MAX_H = 78;

/** Подписи отправителей в партнёрском чате: свои (partner) — без подписи. */
const SENDER_LABEL: Record<ChatMessage["sender_type"], string | null> = {
  user: "Клиент",
  operator: "Оператор",
  partner: null,
  bot: "Бот",
  system: null,
};

// ---------- Сообщение (зеркало ядра Message: свои = partner) ----------

function PartnerMessage({
  message,
  read,
  showSender,
  threadId,
}: {
  message: ChatMessage;
  /** Прочитано клиентом (read-маркер юзера ≥ id) — ✓✓ у своих. */
  read: boolean;
  /** Первое сообщение группы — показать подпись отправителя. */
  showSender: boolean;
  /** Тред для OG-превью внешних ссылок (§8). */
  threadId: string;
}): JSX.Element {
  // свои = партнёр (право), системные/bot — центр-строки — как в ядре
  const own = message.sender_type === "partner";
  const centered = message.sender_type === "system" || message.sender_type === "bot";

  if (message.sender_type === "system") {
    return (
      <div className="chat-msg chat-msg--system">
        <span className="chat-msg__sysline">{message.body}</span>
        <span className="chat-msg__time">{formatMsgTime(message.created_at)}</span>
      </div>
    );
  }

  const soloLink = extractSoloLink(message.body);
  const senderLabel = SENDER_LABEL[message.sender_type];
  const cls = [
    "chat-msg",
    own ? "chat-msg--own" : "chat-msg--other",
    centered ? "chat-msg--centered" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} data-id={message.id}>
      {showSender && senderLabel && !centered && (
        <div className="chat-msg__sender">{senderLabel}</div>
      )}
      <div className="chat-msg__bubble">
        {soloLink && (
          <LinkPreviewCard
            url={soloLink}
            threadId={threadId}
            fetchPreview={fetchPartnerLinkPreview}
          />
        )}
        <Markdown body={message.body} />
        <span className="chat-msg__meta">
          <span className="chat-msg__time">{formatMsgTime(message.created_at)}</span>
          {own && (
            <span
              className="chat-msg__ticks"
              aria-label={read ? "Прочитано" : "Отправлено"}
              data-read={read ? "read" : "sent"}
            >
              {read ? "✓✓" : "✓"}
            </span>
          )}
        </span>
      </div>
    </div>
  );
}

// ---------- Транспорт: ws-токен партнёра + поллинг через papi ----------

/**
 * Подписка на события треда на время жизни компонента — как useChatTransport
 * ядра, но с партнёрскими эндпоинтами: ws-токен кабинета (sub=partner:{id})
 * и поллинг GET /api/partner/chats/{id}/messages?after_id= (X-API-Key).
 */
function usePartnerChatTransport(threadId: string, onEvent: (ev: ChatEvent) => void): void {
  const handlerRef = useRef(onEvent);
  handlerRef.current = onEvent;

  useEffect(() => {
    let stopped = false;
    let transport: ChatTransport | null = null;
    let off: (() => void) | null = null;
    void createChatTransport(threadId, {
      tokenFetcher: fetchPartnerWsToken,
      polling: {
        fetcher: (id, afterId) => fetchPartnerMessages(id, { afterId, limit: 50 }),
      },
    }).then((t) => {
      if (stopped) {
        t.disconnect();
        return;
      }
      transport = t;
      off = t.onEvent((ev) => handlerRef.current(ev));
      t.subscribe(threadChannel(threadId));
    });
    return () => {
      stopped = true;
      off?.();
      transport?.disconnect();
    };
  }, [threadId]);
}

// ---------- Экран чата ----------

export interface PartnerChatRoomProps {
  threadId: string;
  /** Тред из списка (null — рисуем нейтральную шапку). */
  thread?: PartnerThread | null;
  onBack?: () => void;
  /** 401 — ключ отозван/сменён: наверх, на экран входа (X4). */
  onUnauthorized?: () => void;
}

export function PartnerChatRoom({
  threadId,
  thread,
  onBack,
  onUnauthorized,
}: PartnerChatRoomProps) {
  const toast = usePToast();

  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  /** last_read_message_id юзера (✓✓ у своих сообщений ≤ этого id). */
  const [userReadId, setUserReadId] = useState(0);
  const [typing, setTyping] = useState(false);
  /** Группа (08 §7, партнёрский минимум): свой mute + занятость mute/leave. */
  const [muted, setMuted] = useState(false);
  const [govBusy, setGovBusy] = useState(false);
  /** «📄 Выжимка» (12 §3): блок под шапкой; повторный клик — обновление. */
  const [summary, setSummary] = useState<PartnerChatSummary | null>(null);
  const [summaryOpen, setSummaryOpen] = useState(false);
  const [summaryBusy, setSummaryBusy] = useState(false);

  const scrollRef = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLTextAreaElement | null>(null);
  const typingTimer = useRef<number | null>(null);

  /** Ошибка загрузки секции: 401 — наверх, иначе русский текст (04 §7.9). */
  const failText = useCallback(
    (e: unknown): string => {
      if (e instanceof PartnerApiError && e.status === 401) {
        onUnauthorized?.();
        return "Ключ недействителен";
      }
      return partnerFail(e).text;
    },
    [onUnauthorized],
  );

  // Первая загрузка: последние 50 сообщений + сразу читаем тред
  useEffect(() => {
    let alive = true;
    setLoading(true);
    setError(null);
    // Выжимка относится к прошлому треду — сбрасываем (12 §3)
    setSummary(null);
    setSummaryOpen(false);
    fetchPartnerMessages(threadId)
      .then((page) => {
        if (!alive) return;
        const items = [...page.items].sort((a, b) => a.id - b.id);
        setMessages(items);
        setHasMore(page.has_more);
        const last = items[items.length - 1];
        if (last) markPartnerRead(threadId, last.id).catch(() => undefined);
      })
      .catch((e: unknown) => alive && setError(failText(e)))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [threadId, failText]);

  // События транспорта (сокет с партнёрским токеном → поллинг): message/read/typing
  const onEvent = useCallback(
    (ev: ChatEvent) => {
      if (ev.type === "message") {
        const m = ev.message;
        setMessages((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
        if (m.sender_type !== "partner") {
          // входящее чужое — тред открыт, гасим unread
          markPartnerRead(threadId, m.id).catch(() => undefined);
        }
      } else if (ev.type === "read") {
        if (ev.user_type !== "partner") {
          setUserReadId((prev) => Math.max(prev, ev.last_read_message_id));
        }
      } else if (ev.type === "typing") {
        if (ev.user_type !== "partner") {
          setTyping(true);
          if (typingTimer.current !== null) window.clearTimeout(typingTimer.current);
          typingTimer.current = window.setTimeout(() => setTyping(false), 4000);
        }
      }
    },
    [threadId],
  );
  usePartnerChatTransport(threadId, onEvent);

  // Новые сообщения — держим ленту прижатой к низу
  useEffect(() => {
    const el = scrollRef.current;
    if (el && el.scrollTo) el.scrollTo({ top: el.scrollHeight, behavior: "auto" });
  }, [messages.length, typing]);

  useEffect(
    () => () => {
      if (typingTimer.current !== null) window.clearTimeout(typingTimer.current);
    },
    [],
  );

  /** Дозагрузка истории вверх (before_id-пагинация). */
  const loadOlder = async () => {
    const first = messages[0];
    if (loadingOlder || !first) return;
    setLoadingOlder(true);
    const el = scrollRef.current;
    const keep = el?.scrollHeight ?? 0;
    try {
      const page = await fetchPartnerMessages(threadId, { beforeId: first.id });
      const items = [...page.items].sort((a, b) => a.id - b.id).filter((m) => m.id < first.id);
      setMessages((prev) => [...items, ...prev]);
      setHasMore(page.has_more);
      // не даём ленте прыгнуть: держим позицию относительно старого низа
      requestAnimationFrame(() => {
        const el2 = scrollRef.current;
        if (el2 && el2.scrollTo) el2.scrollTo({ top: el2.scrollHeight - keep });
      });
    } catch {
      toast.show("Не удалось загрузить историю", { error: true });
    } finally {
      setLoadingOlder(false);
    }
  };

  /** Отправка: только REST; ответ бэка (MessageView) падает в ленту. */
  const send = async () => {
    const body = input.trim();
    if (!body || sending) return;
    setSending(true);
    try {
      const m = await sendPartnerMessage(threadId, body);
      setMessages((prev) => (prev.some((x) => x.id === m.id) ? prev : [...prev, m]));
      setInput("");
      // авто-рост вернул высоту — сбрасываем после очистки поля
      if (inputRef.current) inputRef.current.style.height = "";
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized?.();
      else toast.show("Сообщение не отправлено — попробуйте ещё раз", { error: true });
    } finally {
      setSending(false);
      inputRef.current?.focus();
    }
  };

  /** Авто-рост textarea до 3 строк: дальше внутренний скролл. */
  const grow = () => {
    const el = inputRef.current;
    if (!el) return;
    el.style.height = "";
    el.style.height = `${Math.min(el.scrollHeight, COMPOSER_MAX_H)}px`;
  };

  // ---------- Группа (08-group-governance §7): партнёрский минимум ----------

  // partner/api не расширяли (тип seller|deal) — группу узнаём свободной проверкой
  const isGroup = (thread as unknown as { type?: string } | null | undefined)?.type === "group";

  /** Свой mute: POST /api/partner/chats/{id}/mute | /unmute (зеркало 08 §6). */
  const toggleMute = async () => {
    if (govBusy) return;
    setGovBusy(true);
    try {
      await papi(`/chats/${threadId}/${muted ? "unmute" : "mute"}`, { method: "POST" });
      setMuted(!muted);
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized?.();
      else toast.show("Не удалось изменить уведомления", { error: true });
    } finally {
      setGovBusy(false);
    }
  };

  /** Выйти из группы: POST /api/partner/chats/{id}/leave → к списку (08 §2). */
  const leave = async () => {
    if (govBusy) return;
    setGovBusy(true);
    try {
      await papi(`/chats/${threadId}/leave`, { method: "POST" });
      onBack?.();
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized?.();
      else toast.show("Не удалось выйти из комнаты", { error: true });
    } finally {
      setGovBusy(false);
    }
  };

  /** «📄 Выжимка» (12 §3): POST …/summary → раскрывающийся блок под шапкой.
   *  Повторный клик обновляет выжимку (бэк кэширует 1ч по last_message_id). */
  const showSummary = async () => {
    if (summaryBusy) return;
    setSummaryOpen(true);
    setSummaryBusy(true);
    try {
      setSummary(await fetchPartnerChatSummary(threadId));
    } catch (e) {
      if (e instanceof PartnerApiError && e.status === 401) onUnauthorized?.();
      else toast.show("Не удалось собрать выжимку — попробуйте ещё раз", { error: true });
    } finally {
      setSummaryBusy(false);
    }
  };

  const closed = thread?.status === "closed";
  const subject = thread?.subject ?? null;

  return (
    <div className="chat-room" data-testid="partner-chat-room">
      <header className="chat-room__head">
        <button
          type="button"
          className="chat-room__back"
          aria-label="К списку чатов"
          onClick={onBack}
        >
          ‹
        </button>
        <div className="chat-room__titles">
          {thread && (
            <span className={`chat-room__type chat-room__type--${thread.type}`}>
              {THREAD_TYPE_LABEL[thread.type]}
            </span>
          )}
          <h3 className="chat-room__title">
            {thread?.title ||
              (subject ? `${subject.make ?? ""} ${subject.model ?? ""}`.trim() : "Чат")}
          </h3>
          {/* кабинет — отдельный роутер: карточка лота открывается вкладкой */}
          {subject && (
            <a
              className="chat-room__subject"
              href={`/listing/${subject.listing_id}`}
              target="_blank"
              rel="noreferrer noopener"
            >
              {subject.image && <img src={subject.image} alt="" loading="lazy" />}
              <span>{[subject.make, subject.model, subject.year].filter(Boolean).join(" ")}</span>
            </a>
          )}
        </div>
        {/* «📄 Выжимка» (12 §3) — всем тредам кабинета; группа ниже добавляет
            mute/«Выйти» (08 §7). */}
        <button
          type="button"
          className="chat-room__summary-btn"
          aria-expanded={summaryOpen}
          disabled={summaryBusy}
          onClick={() => void showSummary()}
        >
          📄 Выжимка
        </button>
        {/* Группа (08 §7): mute-колокольчик + «Выйти» — только партнёрский минимум. */}
        {isGroup && !closed && (
          <>
            <button
              type="button"
              className={muted ? "chat-room__bell chat-room__bell--muted" : "chat-room__bell"}
              data-testid="partner-mute-bell"
              aria-label={muted ? "Уведомления отключены" : "Уведомления включены"}
              disabled={govBusy}
              onClick={() => void toggleMute()}
            >
              {muted ? "🔕" : "🔔"}
            </button>
            <button
              type="button"
              className="chat-room__invite-btn"
              disabled={govBusy}
              onClick={() => void leave()}
            >
              🚪 Выйти
            </button>
          </>
        )}
      </header>

      {/* Раскрывающийся блок выжимки (12 §3): текст + «по состоянию на …» */}
      {summaryOpen && (
        <div className="chat-summary" data-testid="partner-chat-summary">
          {summaryBusy ? (
            <div className="chat-summary__loading">Собираем выжимку…</div>
          ) : summary ? (
            <>
              <div className="chat-summary__head">
                <span className="chat-summary__title">Выжимка диалога</span>
                <span className="chat-summary__as-of">
                  по состоянию на сообщение №{summary.as_of_message_id}
                </span>
              </div>
              <p className="chat-summary__text">{summary.summary}</p>
            </>
          ) : null}
        </div>
      )}

      <div className="chat-room__feed" ref={scrollRef} role="log" aria-label="Сообщения чата">
        {hasMore && (
          <button
            type="button"
            className="chat-room__older"
            disabled={loadingOlder}
            onClick={() => void loadOlder()}
          >
            {loadingOlder ? "Загружаем…" : "Ещё сообщения"}
          </button>
        )}
        {loading && <div className="chat-room__loading">Загружаем сообщения…</div>}
        {error && <div className="chat-room__loading chat-room__loading--error">{error}</div>}
        {!loading &&
          messages.map((m, i) => {
            const prev = messages[i - 1];
            // группа = тот же отправитель подряд (bot/system всегда «сами»)
            const showSender = !prev || prev.sender_type !== m.sender_type;
            return (
              <PartnerMessage
                key={m.id}
                message={m}
                read={m.sender_type === "partner" && m.id <= userReadId}
                showSender={showSender}
                threadId={threadId}
              />
            );
          })}
        {typing && (
          <div className="chat-typing" aria-live="polite">
            <span className="chat-typing__dots" aria-hidden="true">…</span> печатает
          </div>
        )}
      </div>

      <div className="chat-composer">
        <textarea
          ref={inputRef}
          className="chat-composer__input"
          aria-label="Сообщение"
          placeholder={closed ? "Чат закрыт" : "Сообщение… (Enter — отправить)"}
          rows={1}
          maxLength={4000}
          value={input}
          disabled={closed}
          onChange={(e) => {
            setInput(e.target.value);
            grow();
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void send();
            }
          }}
        />
        <button
          type="button"
          className="chat-composer__send"
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

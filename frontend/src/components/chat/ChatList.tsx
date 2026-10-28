/**
 * Список тредов (components/chat, ТЗ §7 + 07 §2 + 08-group-governance §7):
 * поддержка фиксом сверху, затем seller/deal/group по свежести. Строка:
 * subject-миниатюра (переговорные — 👥), тип-бейдж, сниппет плейн-текстом
 * (≤60 симв., markdown-разметка срезается), время, бейдж непрочитанных.
 *
 * Говернанс (08 §6/§7): muted-тред — иконка перечёркнутого колокольчика
 * вместо счётчика (unread не подсвечиваем); закрытые треды уходят в
 * свёрнутую секцию «Архив» внизу (серые, открытие — read-only).
 */

import { useState } from "react";

import type { ChatThread } from "../../lib/chatApi";
import { THREAD_TYPE_LABEL } from "../../lib/chatApi";
import { formatMsgTime } from "./Message";

/** Плейн-текст из markdown-тела: срезаем разметку и «склеиваем» пробелы. */
export function plainSnippet(body: string, max = 60): string {
  const plain = body
    .replace(/```[\s\S]*?```/g, " ") // блоки кода — только шум в сниппете
    .replace(/`([^`]*)`/g, "$1")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[*_~>#]+/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return plain.length > max ? `${plain.slice(0, max).trimEnd()}…` : plain;
}

/** Сортировка списка: support сверху, дальше по времени последнего сообщения. */
export function sortThreads(threads: ChatThread[]): ChatThread[] {
  const rank = (t: ChatThread): number => (t.type === "support" ? 0 : 1);
  const ts = (t: ChatThread): number =>
    t.last_message ? new Date(t.last_message.created_at).getTime() || 0 : 0;
  return [...threads].sort((a, b) => rank(a) - rank(b) || ts(b) - ts(a));
}

export interface ChatListProps {
  threads: ChatThread[];
  /** Открытый тред (подсветка строки). */
  activeId?: string | null;
  onSelect: (id: string) => void;
}

/** Заголовок строки: title → предмет → подпись типа. */
function threadTitle(t: ChatThread): string {
  return (
    t.title ||
    (t.subject ? `${t.subject.make ?? ""} ${t.subject.model ?? ""}`.trim() : "") ||
    THREAD_TYPE_LABEL[t.type]
  );
}

/** Строка треда. muted (08 §6): 🔕 вместо счётчика непрочитанных. */
function ChatItem({
  t,
  activeId,
  archived,
  onSelect,
}: {
  t: ChatThread;
  activeId?: string | null;
  archived?: boolean;
  onSelect: (id: string) => void;
}) {
  const title = threadTitle(t);
  const cls = [
    "chat-item",
    t.id === activeId ? "chat-item--active" : "",
    archived ? "chat-item--archived" : "",
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <li>
      <button
        type="button"
        className={cls}
        onClick={() => onSelect(t.id)}
        aria-label={`Чат: ${title}`}
      >
        {t.subject?.image ? (
          <img className="chat-item__thumb" src={t.subject.image} alt="" loading="lazy" />
        ) : (
          <span className="chat-item__thumb chat-item__thumb--empty" aria-hidden="true">
            {t.is_group
              ? "👥"
              : t.type === "support"
                ? "💬"
                : t.type === "deal"
                  ? "🤝"
                  : "🚗"}
          </span>
        )}
        <span className="chat-item__body">
          <span className="chat-item__top">
            <span className="chat-item__title">{title}</span>
            {archived ? (
              /* Закрытый тред: метка «архив» вместо типа (08 §7) */
              <span className="chat-item__type chat-item__type--archived">архив</span>
            ) : (
              <span className={`chat-item__type chat-item__type--${t.type}`}>
                {THREAD_TYPE_LABEL[t.type]}
              </span>
            )}
          </span>
          {t.last_message && (
            <span className="chat-item__snippet">{plainSnippet(t.last_message.body)}</span>
          )}
        </span>
        <span className="chat-item__side">
          {t.last_message && (
            <span className="chat-item__time">{formatMsgTime(t.last_message.created_at)}</span>
          )}
          {/* muted: колокольчик перечёркнут, счётчик не показываем (08 §6) */}
          {t.muted ? (
            <span className="chat-item__muted" aria-label="Уведомления отключены" title="Уведомления отключены">
              🔕
            </span>
          ) : (
            t.unread > 0 && (
              <span className="chat-item__unread" aria-label={`${t.unread} непрочитанных`}>
                {t.unread > 99 ? "99+" : t.unread}
              </span>
            )
          )}
        </span>
      </button>
    </li>
  );
}

export function ChatList({ threads, activeId, onSelect }: ChatListProps) {
  // Закрытые треды — отдельная свёрнутая секция «Архив» внизу (08 §7)
  const [archiveOpen, setArchiveOpen] = useState(false);
  const active = sortThreads(threads.filter((t) => t.status !== "closed"));
  const archived = sortThreads(threads.filter((t) => t.status === "closed"));

  return (
    <div className="chat-list-wrap">
      <ul className="chat-list" role="list">
        {active.map((t) => (
          <ChatItem key={t.id} t={t} activeId={activeId} onSelect={onSelect} />
        ))}
      </ul>
      {archived.length > 0 && (
        <section className="chat-archive" data-testid="chat-archive" aria-label="Архив чатов">
          <button
            type="button"
            className="chat-archive__head"
            aria-expanded={archiveOpen}
            onClick={() => setArchiveOpen((v) => !v)}
          >
            📁 Архив <span className="chat-archive__count">{archived.length}</span>
            <span className="chat-archive__chevron" aria-hidden="true">
              {archiveOpen ? "▾" : "▸"}
            </span>
          </button>
          {archiveOpen && (
            <ul className="chat-list chat-list--archived" role="list">
              {archived.map((t) => (
                <ChatItem key={t.id} t={t} activeId={activeId} archived onSelect={onSelect} />
              ))}
            </ul>
          )}
        </section>
      )}
    </div>
  );
}

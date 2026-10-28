/**
 * Сообщение чата (components/chat, 05-chats-marketplace.md §7 + 07 §2):
 * - свои (user) — пузырь справа с read-статусом ✓ (отправлено) / ✓✓ (прочитано);
 * - operator/partner — пузырь слева с подписью отправителя;
 * - в переговорных (group) чужие пузыри подписаны именем участника
 *   (senderName от ChatRoom по participants+sender_id);
 * - bot/system — центр-строки (без пузыря, как у ассистента);
 * - тело — markdown (lib/markdown); сообщение из одной ссылки —
 *   превью-карточка над текстом (lib/linkPreview).
 */

import type { ChatMessage } from "../../lib/chatSocket";
import { Markdown } from "../../lib/markdown";
import { extractSoloLink, LinkPreviewCard } from "../../lib/linkPreview";

/** Подписи отправителей (не показываем у своих и system). */
export const SENDER_LABEL: Record<ChatMessage["sender_type"], string | null> = {
  user: null,
  operator: "Оператор",
  partner: "Продавец",
  bot: "Бот",
  system: null,
};

/** Время «14:05» / «14.10» (не сегодня). */
export function formatMsgTime(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = new Date();
  const hm = `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
  const sameDay =
    d.getDate() === now.getDate() &&
    d.getMonth() === now.getMonth() &&
    d.getFullYear() === now.getFullYear();
  if (sameDay) return hm;
  return `${String(d.getDate()).padStart(2, "0")}.${String(d.getMonth() + 1).padStart(2, "0")}`;
}

export interface MessageProps {
  message: ChatMessage;
  /** Прочитано партнёром (last_read_message_id ≥ id) — ✓✓. */
  read: boolean;
  /** Показать подпись отправителя (первое сообщение группы). */
  showSender: boolean;
  /** Имя автора в переговорной (group): перекрывает SENDER_LABEL. */
  senderName?: string | null;
  /** Своё сообщение (в group ChatRoom определяет по sender_id). */
  own?: boolean;
}

export function Message({ message, read, showSender, senderName, own: ownOverride }: MessageProps) {
  const own = ownOverride ?? message.sender_type === "user";
  const centered = message.sender_type === "system" || message.sender_type === "bot";

  // Системные строки — плоский текст (markdown не нужен)
  if (message.sender_type === "system") {
    return (
      <div className="chat-msg chat-msg--system">
        <span className="chat-msg__sysline">{message.body}</span>
        <span className="chat-msg__time">{formatMsgTime(message.created_at)}</span>
      </div>
    );
  }

  const soloLink = extractSoloLink(message.body);
  const senderLabel = senderName ?? SENDER_LABEL[message.sender_type];

  const cls = [
    "chat-msg",
    own ? "chat-msg--own" : "chat-msg--other",
    centered ? "chat-msg--centered" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={cls} data-id={message.id}>
      {showSender && !own && senderLabel && !centered && (
        <div className="chat-msg__sender">{senderLabel}</div>
      )}
      <div className="chat-msg__bubble">
        {soloLink && <LinkPreviewCard url={soloLink} />}
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

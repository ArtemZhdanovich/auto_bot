/**
 * REST-слой чатов (05-chats-marketplace.md §5, 07-sell-chats-partner-
 * inspection.md §2): треды (1:1-диалоги + переговорные group до 5),
 * сообщения, read-маркеры, создание support/seller/group-чатов, инвайты.
 * Всё через api-клиент (JWT + авто-refresh); отправка — только REST.
 */

import { api } from "../api/client";
import type { ChatMessage } from "./chatSocket";

/** Предмет треда — мини-карточка лота (seller/deal/group). */
export interface ChatSubject {
  listing_id: number;
  make: string | null;
  model: string | null;
  year: number | null;
  price: number | null;
  currency: string | null;
  image: string | null;
}

/** Тип треда: support/seller/deal — 1:1-диалоги, group — переговорная. */
export type ChatThreadType = "support" | "seller" | "deal" | "group";

/** Роль участника переговорной (08-group-governance §2): owner|member. */
export type ChatParticipantRole = "owner" | "member";

/** Участник переговорной (GET /api/chats, только у group-тредов). */
export interface ChatParticipant {
  user_type: "user" | "partner";
  user_id: number;
  name: string;
  /** Роль в переговорной (08 §2): owner — создатель (корона в шапке). */
  role?: ChatParticipantRole;
}

/** Тред из GET /api/chats. */
export interface ChatThread {
  id: string;
  type: ChatThreadType;
  title: string;
  status: "active" | "closed";
  unread: number;
  last_message: {
    body: string;
    created_at: string;
    sender_type: ChatMessage["sender_type"];
  } | null;
  subject: ChatSubject | null;
  /** Переговорная (07 §2): title + участники (до 5 включая создателя). */
  is_group?: boolean;
  participants?: ChatParticipant[];
  /** Моя роль в переговорной (08 §2): owner управляет (кик/архив/инвайт). */
  role?: ChatParticipantRole;
  /** Мой mute треда (08 §6): бейдж конверта unread muted не считает. */
  muted?: boolean;
}

/** Подписи типов тредов (единый словарь экранов). */
export const THREAD_TYPE_LABEL: Record<ChatThread["type"], string> = {
  support: "Поддержка",
  seller: "Продавец",
  deal: "Сделка",
  group: "Переговорная",
};

/** Лимит участников переговорной (07 §2: INCLUDING создателя). */
export const GROUP_MAX_MEMBERS = 5;

/** Нормализация participants (07 §2 + 08 §2): user_id числом, name строкой,
 *  role — owner|member (без поля в старых фейках — считаем member). */
function toParticipants(raw: unknown): ChatParticipant[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  return raw
    .map((p): ChatParticipant => {
      const r = (p ?? {}) as Record<string, unknown>;
      return {
        user_type: r.user_type === "partner" ? "partner" : "user",
        user_id: Number(r.user_id ?? 0) || 0,
        name: String(r.name ?? ""),
        role: r.role === "owner" ? "owner" : "member",
      };
    })
    .filter((p) => p.user_id !== 0);
}

/**
 * Нормализация сырого ThreadOut (routers/chats.py): бэк отдаёт subject.id
 * (ядро ждёт listing_id) и last_message.sender (ядро — sender_type).
 * Двойной формат (`listing_id ?? id`, `sender_type ?? sender`) — чтобы
 * старые тесты/фейки с уже-нормализованной формой проходили насквозь.
 */
export function toChatThread(raw: unknown): ChatThread {
  const r = (raw ?? {}) as Record<string, unknown>;
  const s = (r.subject ?? null) as Record<string, unknown> | null;
  const lm = (r.last_message ?? null) as Record<string, unknown> | null;
  const type = (
    r.type === "support" || r.type === "deal" || r.type === "group" ? r.type : "seller"
  ) as ChatThreadType;
  return {
    id: String(r.id ?? ""),
    type,
    title: String(r.title ?? ""),
    status: r.status === "closed" ? "closed" : "active",
    unread: Number(r.unread ?? 0) || 0,
    last_message: lm
      ? {
          body: String(lm.body ?? ""),
          created_at: String(lm.created_at ?? ""),
          sender_type: String(
            lm.sender_type ?? lm.sender ?? "user",
          ) as ChatMessage["sender_type"],
        }
      : null,
    subject: s
      ? {
          listing_id: Number(s.listing_id ?? s.id ?? 0),
          make: (s.make as string) ?? null,
          model: (s.model as string) ?? null,
          year: s.year != null ? Number(s.year) : null,
          price: s.price != null ? Number(s.price) : null,
          currency: (s.currency as string) ?? null,
          image: (s.image as string) ?? null,
        }
      : null,
    is_group: r.is_group === true || type === "group",
    participants: toParticipants(r.participants),
    // 08 §2/§6: моя роль и мой mute (только group; остальным — дефолты)
    role: r.role === "owner" ? "owner" : "member",
    muted: r.muted === true,
  };
}

/** Мои треды (все типы), порядок — от бэка. */
export function fetchChats(): Promise<ChatThread[]> {
  return api<unknown>("/chats").then((raw) =>
    (Array.isArray(raw) ? raw : []).map(toChatThread),
  );
}

/** Страница сообщений (как у партнёрского зеркала): плоский список
 * list[MessageOut] бэка → {items, has_more}; конверт — pass-through. */
export async function fetchMessages(
  threadId: string,
  opts: { beforeId?: number; afterId?: number; limit?: number } = {},
): Promise<{ items: ChatMessage[]; has_more: boolean }> {
  const limit = opts.limit ?? 50;
  const p = new URLSearchParams();
  if (opts.beforeId != null) p.set("before_id", String(opts.beforeId));
  if (opts.afterId != null) p.set("after_id", String(opts.afterId));
  p.set("limit", String(limit));
  const raw = await api<unknown>(`/chats/${threadId}/messages?${p.toString()}`);
  if (Array.isArray(raw)) {
    return { items: raw as ChatMessage[], has_more: raw.length >= limit };
  }
  const page = (raw ?? {}) as { items?: unknown; has_more?: unknown };
  return {
    items: Array.isArray(page.items) ? (page.items as ChatMessage[]) : [],
    has_more: page.has_more === true,
  };
}

/**
 * Ключ идемпотентности отправки (долг ревизора №7): uuid на каждый вызов
 * send — защита от сетевых ретраев прокси/шлюза (повтор запроса с тем же
 * заголовком вернёт сохранённое сообщение, а не дубликат). Фронт сам
 * ретраи не делает; авто-refresh 401 в api-клиенте повторяет запрос с теми
 * же options — то есть с тем же ключом, как и требуется для ретрая.
 */
export function newIdempotencyKey(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") return c.randomUUID();
  // старые рантаймы без crypto.randomUUID — коллизионно-безопасный fallback
  return `idem-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

/** Отправить сообщение (markdown ≤4000) → MessageView. */
export function sendMessage(threadId: string, body: string): Promise<ChatMessage> {
  return api<ChatMessage>(`/chats/${threadId}/messages`, {
    method: "POST",
    headers: { "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({ body }),
  });
}

/** Пометить прочитанным до last_read_message_id включительно; бэк отвечает
 *  200 {"last_read_message_id": N} (без поля — «прочитать всё»). */
export function markRead(threadId: string, lastReadMessageId: number): Promise<void> {
  return api(`/chats/${threadId}/read`, {
    method: "POST",
    body: JSON.stringify({ last_read_message_id: lastReadMessageId }),
  });
}

/**
 * «Я печатаю» (09 §1): POST /api/chats/{id}/typing — бэк публикует typing
 * в личные каналы ДРУГИХ участников. Rate 1/2с на бэке — клиент обязан
 * дебаунсить (createTypingSender, 1.5с) и глушить 429.
 */
export function sendTyping(threadId: string): Promise<void> {
  return api(`/chats/${threadId}/typing`, { method: "POST" });
}

/**
 * Онлайн-статусы юзеров (09 §1): GET /api/chats/online?ids=<tg_id,…> →
 * {online: [telegram_id]} — presence личных каналов (server-side).
 * Пустой список — без запроса (некого спрашивать).
 */
export async function fetchOnlineUsers(ids: number[]): Promise<Set<number>> {
  if (ids.length === 0) return new Set();
  const r = await api<{ online?: unknown }>(`/chats/online?ids=${ids.join(",")}`);
  return new Set(
    Array.isArray(r?.online) ? (r.online as unknown[]).map(Number).filter(Number.isFinite) : [],
  );
}

/** Создать/reuse singleton support-тред. */
export async function createSupportChat(): Promise<string> {
  const r = await api<{ thread_id: string }>("/chats/support", { method: "POST" });
  return r.thread_id;
}

/**
 * «Позвать человека» (support, §6): бот на паузе + system-строка в ленту
 * (приедет событием сокета/поллинга). Идемпотентно: повтор при уже
 * стоящей паузе — тот же 200 без второй system-строки.
 */
export async function summonOperator(
  threadId: string,
): Promise<{ bot_paused: boolean; message: ChatMessage | null }> {
  return api(`/chats/${threadId}/summon-operator`, { method: "POST" });
}

/** Создать/reuse seller-тред по лоту (UNIQUE user+listing). */
export async function createSellerChat(listingId: string | number): Promise<string> {
  const r = await api<{ thread_id: string }>(`/chats/seller/${listingId}`, {
    method: "POST",
  });
  return r.thread_id;
}

// ---------- Переговорные (group, 07-sell-chats-partner-inspection.md §2) ----------

/**
 * Создать переговорную (08 §1): POST /api/chats/group {listing_id, title?}.
 * listing_id теперь ОБЯЗАТЕЛЕН (переговорная создаётся только по автомобилю;
 * 409 group_needs_listing / listing_not_active); пустой title бэк
 * автогенерит «Переговоры: {Make Model Year}». Создатель сразу участник.
 */
export async function createGroupChat(listingId: number, title?: string): Promise<string> {
  const t = (title ?? "").trim();
  const r = await api<{ thread_id: string }>("/chats/group", {
    method: "POST",
    body: JSON.stringify({ listing_id: listingId, ...(t ? { title: t } : {}) }),
  });
  return r.thread_id;
}

/**
 * Инвайт-код переговорной: POST /api/chats/{id}/invite → {invite_code}
 * (6 символов, TTL 7д, многоразовый до лимита участников).
 */
export async function createChatInvite(threadId: string): Promise<string> {
  const r = await api<{ invite_code: string }>(`/chats/${threadId}/invite`, {
    method: "POST",
  });
  return r.invite_code;
}

/**
 * Ошибка «переговорная заполнена»: 409 с detail, содержащим
 * `group_is_full` (контракт §2 — проверяем по подстроке, а не точному JSON).
 */
export function isGroupFullError(e: unknown): boolean {
  return e instanceof Error && /group_is_full/i.test(e.message);
}

/** Присоединиться по коду: POST /api/chats/join {code} → {thread_id}.
 *  409 group_is_full → ошибка (проверяй isGroupFullError), 404 — код истёк. */
export async function joinGroupChat(code: string): Promise<string> {
  const r = await api<{ thread_id: string }>("/chats/join", {
    method: "POST",
    body: JSON.stringify({ code: code.trim() }),
  });
  return r.thread_id;
}

// ---------- Говернанс переговорных (08-group-governance.md §2/§4/§6) ----------

/** Причины жалобы на комнату (08 §4): чипы в панели «Пожаловаться». */
export const CHAT_REPORT_REASONS = ["spam", "fraud", "offtop", "abuse"] as const;
export type ChatReportReason = (typeof CHAT_REPORT_REASONS)[number];

/** Подписи причин жалоб (единый словарь UI). */
export const CHAT_REPORT_REASON_LABEL: Record<ChatReportReason, string> = {
  spam: "Спам",
  fraud: "Мошенничество",
  offtop: "Не по теме",
  abuse: "Оскорбления",
};

/**
 * Свои уведомления треда: POST /api/chats/{id}/mute | /unmute → {muted}.
 * Бэк отвечает итоговым состоянием; без поля (старые фейки) — считаем
 * запрошенное (идемпотентная операция на своей строке участника).
 */
export async function setChatMuted(threadId: string, muted: boolean): Promise<boolean> {
  const r = await api<{ muted?: boolean }>(`/chats/${threadId}/${muted ? "mute" : "unmute"}`, {
    method: "POST",
  });
  return typeof r?.muted === "boolean" ? r.muted : muted;
}

/**
 * Выйти из переговорной: POST /api/chats/{id}/leave. Member — свободно;
 * owner — только последний участник, иначе 409 `owner_must_archive`.
 */
export async function leaveChat(threadId: string): Promise<void> {
  await api(`/chats/${threadId}/leave`, { method: "POST" });
}

/**
 * Архивировать переговорную (owner): POST /api/chats/{id}/archive →
 * status='closed' (read-only: POST messages → 403 thread_archived).
 */
export async function archiveChat(threadId: string): Promise<void> {
  await api(`/chats/${threadId}/archive`, { method: "POST" });
}

/**
 * Кик участника (owner кикает member): DELETE /api/chats/{id}/participants/
 * {user_type}/{user_id}. Partner-строка кикается так же (08 §2).
 */
export async function kickChatParticipant(
  threadId: string,
  userType: ChatParticipant["user_type"],
  userId: number,
): Promise<void> {
  await api(`/chats/${threadId}/participants/${userType}/${userId}`, { method: "DELETE" });
}

/**
 * Пожаловаться на комнату: POST /api/chats/{id}/report {reason, comment?}.
 * 201 первый репорт / 200 повтор (идемпотентен на (reporter,thread,reason)).
 */
export async function reportChat(
  threadId: string,
  reason: ChatReportReason,
  comment?: string,
): Promise<void> {
  const c = (comment ?? "").trim();
  await api(`/chats/${threadId}/report`, {
    method: "POST",
    body: JSON.stringify({ reason, ...(c ? { comment: c } : {}) }),
  });
}

/** 409 «owner не может выйти, пока есть участники» (08 §2). */
export function isOwnerMustArchiveError(e: unknown): boolean {
  return e instanceof Error && /owner_must_archive/i.test(e.message);
}

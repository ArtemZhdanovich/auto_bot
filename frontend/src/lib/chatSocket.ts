/**
 * Транспорт чатов (05-chats-marketplace.md §4 + 09-personal-channel.md §1):
 * Centrifugo-сокет с фоллбэком на поллинг REST. Один интерфейс событий для
 * обоих режимов — payload-типы идентичны (сокет и ?after_id=-поллинг).
 *
 * Два режима фабрики createChatTransport:
 * - thread-режим (устаревший,partner-кабинет): createChatTransport(threadId)
 *   — подписка thread:{id}; фоллбэк PollingTransport крутит after_id треда.
 * - личный режим (мини-апп, 09 §1): createChatTransport({userChannel}) —
 *   подписка ТОЛЬКО user:{uid}; события несут thread_id; фоллбэк
 *   UserChannelPollingTransport: раз в 5с GET /api/chats (список тредов,
 *   уходит в onThreads) + after_id ОТКРЫТОГО треда (setActiveThread).
 *
 * - WsTransport: официальный клиент centrifuge (авто-reconnect на его
 *   стороне); connect() резолвится по client.ready(), не дольше 5с.
 * - Отправка сообщений НЕ через сокет — только REST (POST /messages).
 *
 * Фоллбэк включается: env-флаг VITE_CHAT_POLLING=1 ИЛИ если сокет не
 * подключился за 5с / ws-token недоступен.
 */

import { Centrifuge, type Subscription } from "centrifuge";

import { api } from "../api/client";

// ---------- Payload-типы сокета (контракт фиксирован ТЗ §4) ----------

/** Сообщение чата (MessageView REST-контракта). */
export interface ChatMessage {
  id: number;
  sender_type: "user" | "operator" | "partner" | "bot" | "system";
  sender_id: number | null;
  /** markdown, ≤4000 символов */
  body: string;
  created_at: string;
}

/** События каналов thread:{uuid} | user:{uid} (и поллинга).
 *  thread_id обязателен в личном канале (09 §1: бэк публикует события
 *  треда в личные каналы участников); в thread-канале (partner) поля нет.
 *  listing_status (10 §3) — сменa статуса объявления владельца: приходит
 *  в тот же личный канал user:{uid}, к чатам отношения не имеет. */
export type ChatEvent =
  | { type: "message"; thread_id?: string; message: ChatMessage }
  | { type: "read"; thread_id?: string; user_type: string; last_read_message_id: number }
  | { type: "typing"; thread_id?: string; user_type: string }
  /** Лот треда снят с продажи (экспиратор, 09 §1). */
  | { type: "lot_expired"; thread_id: string; listing_id: number }
  /** Смена статуса объявления (submit/решение/expired/archived, 10 §3). */
  | { type: "listing_status"; listing_id: number; status: string; reason?: string };

export type ChatEventHandler = (ev: ChatEvent) => void;

/** Канал треда (uuid = chat_threads.id). */
export const threadChannel = (threadId: string): string => `thread:${threadId}`;

/** Личный канал юзера (centrifugo sub=user:{telegram_id}). */
export const userChannel = (uid: number | string): string => `user:${uid}`;

// ---------- Нормализация payload-ов бэка (§4: общий формат сокета и REST) ----

/**
 * Publication бэка — ПЛОСКИЙ payload `{type:'message', thread_id, id,
 * sender_type, sender_id, body, created_at}` (app/application/chat/
 * use_cases.message_event); вложенную форму `{type:'message', message:{…}}`
 * поддерживаем как pass-through (внутренние тесты/будущие обёртки).
 * `read`/`typing` в обеих формах уже совпадают по полям; thread_id (личный
 * канал, 09 §1) и lot_expired проходим насквозь.
 */
export function normalizeChatEvent(raw: unknown): ChatEvent | null {
  const ev = (raw ?? {}) as Record<string, unknown>;
  if (typeof ev.type !== "string") return null;
  const threadId = typeof ev.thread_id === "string" ? ev.thread_id : undefined;
  if (ev.type === "message") {
    const m = (ev.message ?? ev) as Record<string, unknown>;
    if (
      typeof m.id === "number" &&
      typeof m.sender_type === "string" &&
      typeof m.body === "string" &&
      typeof m.created_at === "string"
    ) {
      return {
        type: "message",
        ...(threadId ? { thread_id: threadId } : {}),
        message: {
          id: m.id,
          sender_type: m.sender_type as ChatMessage["sender_type"],
          sender_id: (m.sender_id as number | null) ?? null,
          body: m.body,
          created_at: m.created_at,
        },
      };
    }
    return null;
  }
  if (ev.type === "lot_expired") {
    if (!threadId) return null;
    return { type: "lot_expired", thread_id: threadId, listing_id: Number(ev.listing_id ?? 0) };
  }
  // listing_status (10 §3): {type, listing_id, status, reason?} — без thread_id
  if (ev.type === "listing_status") {
    const listingId = Number(ev.listing_id ?? 0);
    if (!Number.isInteger(listingId) || listingId <= 0) return null;
    if (typeof ev.status !== "string" || !ev.status) return null;
    return {
      type: "listing_status",
      listing_id: listingId,
      status: ev.status,
      ...(typeof ev.reason === "string" ? { reason: ev.reason } : {}),
    };
  }
  if (ev.type === "read" || ev.type === "typing") {
    return {
      type: ev.type,
      ...(threadId ? { thread_id: threadId } : {}),
      user_type: String(ev.user_type ?? ""),
      ...(ev.type === "read"
        ? { last_read_message_id: Number(ev.last_read_message_id ?? 0) }
        : {}),
    } as ChatEvent;
  }
  return null;
}

// ---------- Общий интерфейс транспорта ----------

export interface ChatTransport {
  /** Режим доставки (для индикаторов/диагностики). */
  readonly kind: "ws" | "polling";
  /** Подключиться; reject — только у ws (тогда фронт уходит в поллинг). */
  connect(): Promise<void>;
  /** Подписка на канал thread:{uuid} | user:{uid}. */
  subscribe(channel: string): void;
  unsubscribe(channel: string): void;
  /** Зарегистрировать обработчик событий; вернуть функцию отписки. */
  onEvent(handler: ChatEventHandler): () => void;
  disconnect(): void;
}

/** Рассыльщик событий — общая часть обоих транспортов. */
abstract class BaseTransport implements ChatTransport {
  abstract readonly kind: "ws" | "polling";
  protected handlers = new Set<ChatEventHandler>();

  abstract connect(): Promise<void>;
  abstract subscribe(channel: string): void;
  abstract unsubscribe(channel: string): void;
  abstract disconnect(): void;

  onEvent(handler: ChatEventHandler): () => void {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  protected emit(ev: ChatEvent): void {
    this.handlers.forEach((h) => h(ev));
  }
}

// ---------- WebSocket-транспорт (centrifuge) ----------

/** Сколько ждём первого подключения сокета до фоллбэка в поллинг. */
const WS_CONNECT_TIMEOUT_MS = 5000;

export class WsTransport extends BaseTransport {
  readonly kind = "ws" as const;
  private client: Centrifuge | null = null;
  private subs = new Map<string, Subscription>();

  constructor(
    private url: string,
    private token: string,
  ) {
    super();
  }

  connect(): Promise<void> {
    // Клиент centrifuge сам переподключается (reconnect backoff) и сам
    // обновляет подписки; нам важен только факт первого коннекта.
    this.client = new Centrifuge(this.url, { token: this.token });
    this.client.connect();
    return this.client.ready(WS_CONNECT_TIMEOUT_MS).then(() => undefined);
  }

  subscribe(channel: string): void {
    const c = this.client;
    if (!c || this.subs.has(channel)) return;
    // Один подписчик на канал в реестре клиента — reuse, иначе create
    const sub = c.getSubscription(channel) ?? c.newSubscription(channel);
    sub.on("publication", (ctx: { data: unknown }) => {
      const ev = normalizeChatEvent(ctx.data);
      if (ev) this.emit(ev);
    });
    sub.subscribe();
    this.subs.set(channel, sub);
  }

  unsubscribe(channel: string): void {
    const sub = this.subs.get(channel);
    if (!sub) return;
    sub.removeAllListeners("publication");
    sub.unsubscribe();
    this.client?.removeSubscription(sub);
    this.subs.delete(channel);
  }

  disconnect(): void {
    for (const ch of [...this.subs.keys()]) this.unsubscribe(ch);
    this.client?.disconnect();
    this.client = null;
  }
}

// ---------- Поллинг-транспорт (фоллбэк) ----------

/** Ответ GET /api/chats/{id}/messages (пагинация down + поллинг). */
export interface MessagesPage {
  items: ChatMessage[];
  has_more: boolean;
}

/** GET /api/chats/{id}/messages?after_id= — через api-клиент (JWT+refresh).
 * Бэк отдаёт плоский list[MessageOut] (§5) — нормализуем в MessagesPage
 * (has_more по полноте страницы); конверт {items,has_more} — pass-through. */
const defaultFetcher = async (threadId: string, afterId: number): Promise<MessagesPage> => {
  const raw = await api<unknown>(`/chats/${threadId}/messages?after_id=${afterId}&limit=50`);
  if (Array.isArray(raw)) return { items: raw as ChatMessage[], has_more: raw.length >= 50 };
  const page = (raw ?? {}) as { items?: unknown; has_more?: unknown };
  return {
    items: Array.isArray(page.items) ? (page.items as ChatMessage[]) : [],
    has_more: page.has_more === true,
  };
};

export interface PollingOptions {
  /** Интервал опроса (тесты уменьшают). */
  intervalMs?: number;
  /** GET-загрузчик страницы (тесты подменяют). */
  fetcher?: (threadId: string, afterId: number) => Promise<MessagesPage>;
}

export class PollingTransport extends BaseTransport {
  readonly kind = "polling" as const;
  private timer: number | null = null;
  private afterId = 0;
  /** Поллинг обслуживает только свой тред (каналы user:* игнорирует). */
  private active = false;
  private readonly intervalMs: number;
  private readonly fetcher: (threadId: string, afterId: number) => Promise<MessagesPage>;

  constructor(
    private threadId: string,
    opts: PollingOptions = {},
  ) {
    super();
    this.intervalMs = opts.intervalMs ?? 5000;
    this.fetcher = opts.fetcher ?? defaultFetcher;
  }

  connect(): Promise<void> {
    if (this.timer === null) {
      // первый тик сразу — не ждём интервала после открытия экрана
      void this.tick();
      this.timer = window.setInterval(() => void this.tick(), this.intervalMs);
    }
    return Promise.resolve();
  }

  subscribe(channel: string): void {
    if (channel === threadChannel(this.threadId)) this.active = true;
  }

  unsubscribe(channel: string): void {
    if (channel === threadChannel(this.threadId)) this.active = false;
  }

  disconnect(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
    this.active = false;
  }

  private async tick(): Promise<void> {
    if (!this.active) return;
    try {
      const page = await this.fetcher(this.threadId, this.afterId);
      // порядок не гарантирован — сортируем и двигаем курсор after_id
      const fresh = [...page.items].sort((a, b) => a.id - b.id);
      for (const m of fresh) {
        if (m.id > this.afterId) {
          this.afterId = m.id;
          this.emit({ type: "message", message: m });
        }
      }
    } catch {
      // сеть/401 — молча: следующий тик повторит (авто-refresh в api-клиенте)
    }
  }
}

// ---------- Личный одноканальный транспорт (09-personal-channel.md §1) ----------

/**
 * Транспорт личного канала user:{uid}: события всех тредов юзера в одной
 * подписке. Дополнительно к ChatTransport:
 * - onThreads — обновление списка тредов (поллинг-фоллбэк дергает
 *   GET /api/chats каждые 5с; ws-режим список не поллит — события живые);
 * - setActiveThread — открытая комната для after_id-поллинга фоллбэка
 *   (null — комната закрыта, дергаем только список).
 */
export interface UserChannelTransport extends ChatTransport {
  onThreads(handler: (raw: unknown) => void): () => void;
  setActiveThread(threadId: string | null, afterId: number): void;
}

/** Опции поллинг-фоллбэка личного канала (DI для тестов). */
export interface UserPollingOptions {
  /** Интервал опроса (тесты уменьшают). */
  intervalMs?: number;
  /** GET /api/chats — сырой ответ (список нормализует стор). */
  listFetcher?: () => Promise<unknown>;
  /** GET /api/chats/{id}/messages?after_id= открытого треда. */
  messagesFetcher?: (threadId: string, afterId: number) => Promise<MessagesPage>;
}

const defaultListFetcher = (): Promise<unknown> => api("/chats");

/** Поллинг-фоллбэк личного канала: список тредов + after_id открытого. */
export class UserChannelPollingTransport
  implements UserChannelTransport
{
  readonly kind = "polling" as const;
  private timer: number | null = null;
  private handlers = new Set<ChatEventHandler>();
  private threadsHandlers = new Set<(raw: unknown) => void>();
  /** Подписан ли личный канал (каналы thread:* игнорируем). */
  private active = false;
  /** Открытая комната + курсор after_id (null — комнаты нет). */
  private activeThread: { threadId: string; afterId: number } | null = null;
  private readonly intervalMs: number;
  private readonly listFetcher: () => Promise<unknown>;
  private readonly messagesFetcher: (threadId: string, afterId: number) => Promise<MessagesPage>;

  constructor(
    private channel: string,
    opts: UserPollingOptions = {},
  ) {
    this.intervalMs = opts.intervalMs ?? 5000;
    this.listFetcher = opts.listFetcher ?? defaultListFetcher;
    this.messagesFetcher = opts.messagesFetcher ?? defaultFetcher;
  }

  connect(): Promise<void> {
    if (this.timer === null) {
      // первый тик сразу — не ждём интервала после старта
      void this.tick();
      this.timer = window.setInterval(() => void this.tick(), this.intervalMs);
    }
    return Promise.resolve();
  }

  subscribe(channel: string): void {
    if (channel === this.channel) this.active = true;
  }

  unsubscribe(channel: string): void {
    if (channel === this.channel) this.active = false;
  }

  onEvent(handler: ChatEventHandler): () => void {
    this.handlers.add(handler);
    return () => {
      this.handlers.delete(handler);
    };
  }

  onThreads(handler: (raw: unknown) => void): () => void {
    this.threadsHandlers.add(handler);
    return () => {
      this.threadsHandlers.delete(handler);
    };
  }

  /** Стор сообщает открытую комнату и последний известный id сообщения. */
  setActiveThread(threadId: string | null, afterId: number): void {
    this.activeThread = threadId === null ? null : { threadId, afterId };
  }

  disconnect(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
    this.active = false;
    this.activeThread = null;
  }

  private async tick(): Promise<void> {
    if (!this.active) return;
    // 1) список тредов: unread/статусы/новые треды (стор нормализует)
    try {
      const raw = await this.listFetcher();
      this.threadsHandlers.forEach((h) => h(raw));
    } catch {
      // сеть/401 — молча: следующий тик повторит (авто-refresh в api-клиенте)
    }
    // 2) открытая комната: свежие сообщения после курсора
    const at = this.activeThread;
    if (!at) return;
    try {
      const page = await this.messagesFetcher(at.threadId, at.afterId);
      const fresh = [...page.items].sort((a, b) => a.id - b.id);
      for (const m of fresh) {
        if (m.id > at.afterId) {
          at.afterId = m.id;
          this.handlers.forEach((h) =>
            h({ type: "message", thread_id: at.threadId, message: m }),
          );
        }
      }
    } catch {
      // молча — следующий тик повторит
    }
  }
}

/** ws-режим личного канала: только сокет, список/after_id не поллим
 *  (события приходят сами; onThreads/setActiveThread — no-op). */
class UserChannelWsTransport implements UserChannelTransport {
  readonly kind = "ws" as const;
  constructor(private ws: WsTransport) {}

  connect(): Promise<void> {
    return this.ws.connect();
  }
  subscribe(channel: string): void {
    this.ws.subscribe(channel);
  }
  unsubscribe(channel: string): void {
    this.ws.unsubscribe(channel);
  }
  onEvent(handler: ChatEventHandler): () => void {
    return this.ws.onEvent(handler);
  }
  onThreads(): () => void {
    return () => undefined;
  }
  setActiveThread(): void {
    // ws-режиму курсор не нужен — события доставки не теряются
  }
  disconnect(): void {
    this.ws.disconnect();
  }
}

// ---------- Фабрика транспортов ----------

/** GET /api/chats/ws-token → {token, url} (DI для тестов). */
type TokenFetcher = () => Promise<{ token: string; url: string }>;

const defaultTokenFetcher: TokenFetcher = () =>
  api<{ token: string; url: string }>("/chats/ws-token");

/** env-флаг принудительного поллинга (бот-режимы WebView без ws). */
export function pollingForced(): boolean {
  try {
    return (
      (import.meta as unknown as { env?: Record<string, string> }).env
        ?.VITE_CHAT_POLLING === "1"
    );
  } catch {
    return false;
  }
}

/** Опции thread-режима (устаревший интерфейс партнёрского кабинета). */
export interface ThreadTransportOptions {
  tokenFetcher?: TokenFetcher;
  polling?: PollingOptions;
  forcePolling?: boolean;
}

/** Опции личного режима (мини-апп, 09 §1). */
export interface UserChannelTransportOptions {
  tokenFetcher?: TokenFetcher;
  polling?: UserPollingOptions;
  forcePolling?: boolean;
}

/**
 * Транспорт для экрана чата: ws-сокет, при неудаче (нет токена, не
 * подключился за 5с, env-флаг / forcePolling) — поллинг того же треда.
 * forcePolling — тот же переключатель вне сборки (тесты/WebView-режимы).
 */
export async function createChatTransport(
  threadId: string,
  opts?: ThreadTransportOptions,
): Promise<ChatTransport>;
/**
 * Личный одноканальный транспорт (09 §1): подписка ТОЛЬКО user:{uid}
 * (подписывается сама фабрика), фоллбэк — поллинг списка + after_id
 * открытого треда.
 */
export async function createChatTransport(
  target: { userChannel: string },
  opts?: UserChannelTransportOptions,
): Promise<UserChannelTransport>;
export async function createChatTransport(
  target: string | { userChannel: string },
  opts: ThreadTransportOptions & UserChannelTransportOptions = {},
): Promise<ChatTransport | UserChannelTransport> {
  // Личный режим: один канал user:{uid}, фоллбэк — список + after_id.
  if (typeof target === "object" && target !== null) {
    if (opts.forcePolling || pollingForced()) {
      return new UserChannelPollingTransport(target.userChannel, opts.polling);
    }
    try {
      const { token, url } = await (opts.tokenFetcher ?? defaultTokenFetcher)();
      const ws = new WsTransport(url, token);
      await ws.connect();
      // Единственная подписка мини-аппа — личный канал юзера.
      ws.subscribe(target.userChannel);
      return new UserChannelWsTransport(ws);
    } catch {
      // ws-token недоступен или коннект не удался — фоллбэк на поллинг
      return new UserChannelPollingTransport(target.userChannel, opts.polling);
    }
  }
  // Thread-режим (партнёрский кабинет): подписка thread:{id} вызывающим.
  if (opts.forcePolling || pollingForced()) {
    return new PollingTransport(target, opts.polling);
  }
  try {
    const { token, url } = await (opts.tokenFetcher ?? defaultTokenFetcher)();
    const ws = new WsTransport(url, token);
    await ws.connect();
    return ws;
  } catch {
    // ws-token недоступен или коннект не удался — фоллбэк на поллинг
    return new PollingTransport(target, opts.polling);
  }
}

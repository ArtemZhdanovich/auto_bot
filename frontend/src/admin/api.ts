/**
 * HTTP-клиент админ-панели: аутентификация по X-Internal-Token
 * (MVP, UI/UX ТЗ §3.1; таблица admin_users + JWT — [бэкенд]).
 */

const ADMIN_TOKEN = "autobot.adminToken";

export function getAdminToken(): string | null {
  return sessionStorage.getItem(ADMIN_TOKEN);
}

export function setAdminToken(token: string): void {
  sessionStorage.setItem(ADMIN_TOKEN, token);
}

export function clearAdminToken(): void {
  sessionStorage.removeItem(ADMIN_TOKEN);
}

export class AdminApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function aapi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const token = getAdminToken();
  if (!token) throw new AdminApiError(401, "Нет токена");
  const resp = await fetch(`/api/admin${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Internal-Token": token,
      ...((options.headers as Record<string, string>) ?? {}),
    },
  });
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : resp.statusText;
    throw new AdminApiError(resp.status, detail);
  }
  return body as T;
}

// --- Типы (контракты app/schemas/criteria.py, partner.py) ---

export interface SourceConfig {
  name: string;
  adapter_class: string;
  kind: "api" | "parser" | "hybrid";
  enabled: boolean;
  priority: number;
  schedule_cron: string | null;
  rate_limit_per_min: number;
  max_concurrency: number;
  timeout_sec: number;
  retry_policy: Record<string, unknown>;
  credentials_ref: string | null;
  capabilities: Record<string, unknown>;
  config: Record<string, unknown>;
}

export interface SourceHealth {
  source_name: string;
  last_success_at: string | null;
  last_error_at: string | null;
  error_streak: number;
  last_error: string | null;
  avg_latency_ms: number | null;
  total_fetched: number;
}

export interface AdminPartner {
  id: number;
  name: string;
  contact: string | null;
  plan: "starter" | "pro" | "salon";
  daily_quota: number;
  status: string;
  created_at: string;
  claimed_today: number;
  key_prefix: string | null;
}

export interface PartnerKeyOut {
  partner_id: number;
  key: string;
  key_prefix: string;
}

export interface AdminLead {
  id: number;
  user_id: number;
  listing_id: number | null;
  source: string | null;
  status: string;
  partner_id: number | null;
  partner_status: string | null;
  comment: string | null;
  qualification: Record<string, unknown> | null;
  created_at: string;
}

// --- Чаты поддержки (операторский контур, 05-chats-marketplace.md §2/§6) ---

/** Сообщение чата (MessageOut бэка — общий контракт с ядром чатов). */
export interface AdminChatMessage {
  id: number;
  sender_type: "user" | "operator" | "partner" | "bot" | "system";
  sender_id: number | null;
  body: string;
  created_at: string;
}

/** Тред в списке оператора (GET /api/admin/chats). */
export interface AdminSupportThread {
  id: string;
  type: string;
  status: "active" | "closed";
  bot_paused: boolean;
  user_id: number;
  user_name: string | null;
  /** Последнее сообщение юзера (время + сниппет без markdown). */
  last_user_message_at: string | null;
  last_user_message_snippet: string | null;
}

/** GET /api/admin/chats — список тредов (по умолчанию support). */
export function fetchAdminChats(
  opts: { type?: "support" | "seller" | "deal"; paused?: boolean; limit?: number } = {},
): Promise<AdminSupportThread[]> {
  const p = new URLSearchParams();
  if (opts.type) p.set("type", opts.type);
  if (opts.paused != null) p.set("paused", String(opts.paused));
  p.set("limit", String(opts.limit ?? 50));
  return aapi<AdminSupportThread[]>(`/chats?${p.toString()}`);
}

/** GET /api/admin/chats/unread — сколько тредов ждут ответа оператора. */
export async function fetchAdminUnreadChats(
  opts: { type?: "support" | "seller" | "deal" } = {},
): Promise<number> {
  const p = new URLSearchParams();
  if (opts.type) p.set("type", opts.type);
  const r = await aapi<{ threads: number }>(`/chats/unread?${p.toString()}`);
  return Number(r.threads) || 0;
}

/**
 * GET /api/admin/chats/{id}/messages?before_id=|after_id=&limit=50 —
 * плоский list[MessageOut] → {items, has_more} (как ядро чатов).
 */
export async function fetchAdminMessages(
  threadId: string,
  opts: { beforeId?: number; afterId?: number; limit?: number } = {},
): Promise<{ items: AdminChatMessage[]; has_more: boolean }> {
  const limit = opts.limit ?? 50;
  const p = new URLSearchParams();
  if (opts.beforeId != null) p.set("before_id", String(opts.beforeId));
  if (opts.afterId != null) p.set("after_id", String(opts.afterId));
  p.set("limit", String(limit));
  const raw = await aapi<unknown>(`/chats/${threadId}/messages?${p.toString()}`);
  const items = Array.isArray(raw) ? (raw as AdminChatMessage[]) : [];
  return { items, has_more: items.length >= limit };
}

/** POST /api/admin/chats/{id}/messages {body} — ответ оператора (markdown). */
export function sendAdminMessage(
  threadId: string,
  body: string,
): Promise<AdminChatMessage> {
  return aapi<AdminChatMessage>(`/chats/${threadId}/messages`, {
    method: "POST",
    body: JSON.stringify({ body }),
  });
}

// --- Выжимка диалога (12-ai-trust.md §3) ---

/** Ответ POST /api/admin/chats/{id}/summary — LLM-выжимка диалога
 *  (≤600 знаков: что хочет клиент, лоты, сомнения, следующий шаг). */
export interface AdminChatSummary {
  summary: string;
  /** Выжимка покрывает сообщения до этого id включительно. */
  as_of_message_id: number;
}

/** POST /api/admin/chats/{id}/summary — выжимка последних ≤50 сообщений
 *  (кэш бэка 1ч по last_message_id — повторный клик дёшев). */
export function fetchAdminChatSummary(threadId: string): Promise<AdminChatSummary> {
  return aapi<AdminChatSummary>(`/chats/${threadId}/summary`, { method: "POST" });
}

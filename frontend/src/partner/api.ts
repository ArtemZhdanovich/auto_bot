/**
 * HTTP-клиент партнёрского ЛК: аутентификация по API-ключу
 * (X-API-Key, UI/UX ТЗ §4.1 — вход только по ключу).
 *
 * Редизайн 04-partner-redesign:
 * - X5: ключ хранится в localStorage при «Запомнить на этом устройстве»
 *   (по умолчанию — прежнее поведение, sessionStorage);
 * - X2: partnerFail() — единый маппинг статусов API на русские строки §7.9;
 * - D1/B1: типы дополнены фактическим контрактом (balance/cpl_cost).
 *
 * Редизайн 05-chats-marketplace (F2):
 * - чаты счёта: GET /api/partner/chats, GET/POST …/messages, POST …/read,
 *   GET …/ws-token (JWT sub=partner:{id}) — зеркала user-контрактов §5;
 * - витрина: GET/POST /api/partner/offers, DELETE …/offers/{listing_id}.
 */

import type { ChatMessage } from "../lib/chatSocket";
import { newIdempotencyKey, type ChatSubject } from "../lib/chatApi";
import type { LinkPreviewData, LinkPreviewFetcher } from "../lib/linkPreview";
import type { CreatedLot, MarketPackage, MyLot } from "../api/types";

const PARTNER_KEY = "autobot.partnerKey";

/** Где хранится ключ: true → localStorage («запомнили»), false → sessionStorage. */
export function isPartnerKeyRemembered(): boolean {
  try {
    return localStorage.getItem(PARTNER_KEY) !== null;
  } catch {
    return false;
  }
}

export function getPartnerKey(): string | null {
  try {
    return sessionStorage.getItem(PARTNER_KEY) ?? localStorage.getItem(PARTNER_KEY);
  } catch {
    return null;
  }
}

/** Сохранить ключ; remember=true — переживает закрытие браузера (X5). */
export function setPartnerKey(key: string, remember: boolean): void {
  clearPartnerKey();
  try {
    (remember ? localStorage : sessionStorage).setItem(PARTNER_KEY, key);
  } catch {
    /* private mode — ключ живёт только в памяти запроса */
  }
}

export function clearPartnerKey(): void {
  try {
    sessionStorage.removeItem(PARTNER_KEY);
    localStorage.removeItem(PARTNER_KEY);
  } catch {
    /* private mode */
  }
}

export class PartnerApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function papi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const key = getPartnerKey();
  if (!key) throw new PartnerApiError(401, "Нет API-ключа");
  let resp: Response;
  try {
    resp = await fetch(`/api/partner${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        "X-API-Key": key,
        ...((options.headers as Record<string, string>) ?? {}),
      },
    });
  } catch {
    // Сеть недоступна (fetch кидает TypeError) — §7.9 «Сеть»
    throw new PartnerApiError(0, "Нет соединения");
  }
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail = typeof body?.detail === "string" ? body.detail : resp.statusText;
    throw new PartnerApiError(resp.status, detail);
  }
  return body as T;
}

// --- Ошибки: единый маппинг на русские строки (04 §7.9, X2) ---

/** Вид ошибки — какую кнопку-действие предложить в тосте/состоянии. */
export type PartnerFailKind =
  | "auth" // 401 → выход на экран входа
  | "balance" // 402 → «К биллингу» + «Написать менеджеру»
  | "quota" // 429 квота → «К обзору»
  | "rate" // 429 rate limit → без действия
  | "taken" // 404/409 → «Обновить»
  | "server" // 5xx → «Повторить»
  | "network" // сеть → «Обновить»
  | "other"; // прочее → «Повторить»

export interface PartnerFail {
  text: string;
  kind: PartnerFailKind;
}

/** Любое исключение запроса → человекочитаемая строка + вид действия. */
export function partnerFail(e: unknown): PartnerFail {
  if (e instanceof PartnerApiError) {
    switch (e.status) {
      case 401:
        return {
          kind: "auth",
          text: "Ключ недействителен. Возможно, его отозвали или сменили — запросите новый у менеджера.",
        };
      case 402:
        return {
          kind: "balance",
          text: "Недостаточно средств на балансе. Пополните счёт у менеджера, чтобы забирать лиды.",
        };
      case 404:
        return {
          kind: "taken",
          text: "Лид не найден. Возможно, его уже забрал другой партнёр — обновите пул.",
        };
      case 409:
        return {
          kind: "taken",
          text: "Лид уже забрал другой партнёр. Обновите пул — там есть другие.",
        };
      case 429:
        return /quota/i.test(e.message)
          ? { kind: "quota", text: "Дневная квота исчерпана. Она обновится завтра." }
          : { kind: "rate", text: "Слишком много запросов подряд. Подождите минуту." };
      default:
        if (e.status >= 500) {
          return {
            kind: "server",
            text: "Сервис временно недоступен. Повторите через минуту — ваши лиды и баланс не теряются.",
          };
        }
    }
  }
  if (e instanceof TypeError) {
    return { kind: "network", text: "Нет соединения. Проверьте интернет и обновите страницу." };
  }
  return {
    kind: "other",
    text: "Что-то пошло не так. Попробуйте ещё раз — если повторится, напишите менеджеру.",
  };
}

// --- Типы (контракты app/transport/http/schemas/partner.py) ---

export type PartnerPlanKey = "starter" | "pro" | "salon";

/** Человекочитаемое название тарифа (D2: без сырых ключей в UI). */
export const PLAN_TITLES: Record<string, string> = {
  starter: "Starter",
  pro: "Pro",
  salon: "Salon",
};

export function planTitle(plan: string | null | undefined): string {
  return (plan && PLAN_TITLES[plan]) || plan || "—";
}

export interface PartnerMe {
  id: number;
  name: string;
  plan: string;
  daily_quota: number;
  claimed_today: number;
  quota_remaining: number;
  /** CPL-биллинг: деньги на счёте (D1, P2-14). */
  balance: number;
  /** Цена следующего лида, $ (D1). */
  cpl_cost: number;
  key_prefix: string | null;
}

export interface PartnerLead {
  id: number;
  source: string | null;
  status: string;
  comment: string | null;
  qualification: Record<string, unknown> | null;
  created_at: string;
  partner_id?: number | null;
  /** Статус качества: claimed (ждёт отметки) / accepted / returned / junk / deal. */
  partner_status?: string | null;
  claimed_at?: string | null;
  /** PII — отдаётся только после claim (PartnerLeadOwnedOut). */
  user_id?: number | null;
}

export interface ClaimOut {
  lead_id: number;
  partner_id: number;
  partner_status: string | null;
  user_id: number | null;
  qualification: Record<string, unknown> | null;
  claimed_at: string | null;
}

export interface BatchClaimOut {
  claimed: ClaimOut[];
}

// --- Биллинг ---

export interface PlanInfo {
  key: PartnerPlanKey;
  title: string;
  daily_quota: number;
  /** Цена лида тарифа, $ (B4: главный параметр партнёра). */
  cpl_cost: number;
  /** Абонент тарифа, $ (может отсутствовать в каталоге без подписки). */
  price_usd?: number | null;
  description?: string | null;
}

/** Транзакция счёта (появится с GET-хотелкой §5.3-3; сейчас бэкенд не отдаёт). */
export interface PartnerTransaction {
  id?: number | string;
  /** topup / charge / adjustment — нормализуем в «Пополнение/Списание». */
  kind?: string | null;
  amount: number;
  /** ISO-дата. */
  created_at?: string | null;
  comment?: string | null;
}

export interface BillingOut {
  plan: string;
  daily_quota: number;
  claimed_today: number;
  quota_remaining: number;
  balance: number;
  cpl_cost: number;
  plans: PlanInfo[];
  /** Секции нет в API — рендерим только если бэкенд начнёт отдавать (§4.5.3). */
  transactions?: PartnerTransaction[];
}

/**
 * Нормализация /billing: контракт отдаёт balance/cpl_cost и каталог тарифов
 * с cpl_cost/description; transactions — опционально (деградация без них).
 */
export function toBillingOut(raw: unknown): BillingOut {
  const r = (raw ?? {}) as Record<string, unknown>;
  const num = (v: unknown): number => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };
  const plans = (Array.isArray(r.plans) ? r.plans : []) as Record<string, unknown>[];
  const txs = (Array.isArray(r.transactions) ? r.transactions : null) as
    | Record<string, unknown>[]
    | null;
  return {
    plan: String(r.plan ?? ""),
    daily_quota: num(r.daily_quota),
    claimed_today: num(r.claimed_today),
    quota_remaining: num(r.quota_remaining),
    balance: num(r.balance),
    cpl_cost: num(r.cpl_cost),
    plans: plans.map((p) => ({
      key: String(p.key ?? "") as PartnerPlanKey,
      title: String(p.title ?? p.key ?? "—"),
      daily_quota: num(p.daily_quota),
      cpl_cost: num(p.cpl_cost),
      price_usd: p.price_usd != null ? num(p.price_usd) : null,
      description: (p.description as string | null) ?? null,
    })),
    transactions: txs
      ? txs.map((t, i) => ({
          id: (t.id as number | string | undefined) ?? i,
          kind: (t.kind as string | null) ?? null,
          amount: num(t.amount),
          created_at: (t.created_at as string | null) ?? null,
          comment: (t.comment as string | null) ?? null,
        }))
      : undefined,
  };
}

// --- Рынок (GET /api/partner/market?make=&model=) ---

/** Выгодный лот: дешевле медианы рынка (сортировка по выгоде). */
export interface MarketLot {
  id?: number;
  make: string | null;
  model: string | null;
  year: number | null;
  price: number | null;
  currency: string | null;
  /** Насколько лот дешевле рынка, % (для бейджа «−12%»). */
  discount_pct: number | null;
  url: string | null;
}

/** Сводка рынка + выгодные лоты. */
export interface MarketOut {
  count: number;
  min: number | null;
  max: number | null;
  median: number | null;
  items: MarketLot[];
}

/**
 * Нормализация ответа /market: бэкенд может отдать сводку вложенно
 * (summary: {count, min_price, max_price, median_price}) или плоско,
 * лоты — в items или deals (контракт ещё фиксируется).
 */
export function toMarketOut(raw: unknown): MarketOut {
  const r = (raw ?? {}) as Record<string, unknown>;
  const s = (r.summary ?? {}) as Record<string, unknown>;
  const num = (v: unknown): number | null => {
    const n = Number(v);
    return Number.isFinite(n) && n !== 0 ? n : null;
  };
  const items = (Array.isArray(r.items) ? r.items : Array.isArray(r.deals) ? r.deals : []) as Record<
    string,
    unknown
  >[];
  return {
    count: Number(s.count ?? r.count ?? 0) || 0,
    min: num(s.min_price ?? s.min ?? r.min_price ?? r.min),
    max: num(s.max_price ?? s.max ?? r.max_price ?? r.max),
    median: num(s.median_price ?? s.median ?? r.median_price ?? r.median),
    items: items.map((it) => ({
      id: it.id != null ? Number(it.id) : undefined,
      make: (it.make as string) ?? null,
      model: (it.model as string) ?? null,
      year: it.year != null ? Number(it.year) : null,
      price: num(it.price),
      currency: (it.currency as string) ?? null,
      discount_pct: it.discount_pct != null ? Number(it.discount_pct) : null,
      url: (it.url as string) ?? null,
    })),
  };
}

/**
 * Сколько лидов можно взять batch-кнопкой (04 §4.2.2):
 * N = min(остаток квоты, баланс / цена лида, жёсткий потолок 10).
 */
export function batchClaimCap(
  quotaRemaining: number,
  balance: number,
  cplCost: number,
): number {
  const byQuota = Math.max(0, Math.floor(quotaRemaining));
  const byBalance = cplCost > 0 ? Math.floor(balance / cplCost) : 0;
  return Math.max(0, Math.min(byQuota, byBalance, 10));
}

/** «Хватит на N лидов» (D1/B1): balance / cpl, без ограничения квотой. */
export function leadsAffordable(balance: number, cplCost: number): number {
  return cplCost > 0 ? Math.floor(balance / cplCost) : 0;
}

// --- Чаты партнёра (05-chats-marketplace.md §5, зеркала user-контрактов) ---

/** Типы тредов партнёра: у счёта нет support-чатов (§2). */
export type PartnerThreadType = "seller" | "deal";

/**
 * Тред партнёрского чата — те же поля, что user GET /api/chats
 * (title/subject/last_message/unread/status/type), нормализованные под
 * ядро F1 (lib/chatApi.ChatThread): subject.listing_id и
 * last_message.sender_type — см. toPartnerThread.
 */
export interface PartnerThread {
  id: string;
  type: PartnerThreadType;
  title: string;
  status: "active" | "closed";
  unread: number;
  last_message: {
    body: string;
    created_at: string;
    sender_type: ChatMessage["sender_type"];
  } | null;
  subject: ChatSubject | null;
  /** Deal-тред: лид заявки (CTA «Отметить сделкой», долг ревизора №6). */
  lead_id: number | null;
  /** Партнёрский статус лида: claimed/accepted/returned/junk/deal. */
  lead_status: string | null;
}

/**
 * Нормализация сырого ThreadOut (routers/market.py partner_chats):
 * - бэк отдаёт subject.id, ядро F1 (ChatList/ChatRoom) ждёт listing_id;
 * - бэк отдаёт last_message.sender, ядро — sender_type;
 * - type вне seller/deal (не должно быть по правам §2) — считаем seller.
 */
export function toPartnerThread(raw: unknown): PartnerThread {
  const r = (raw ?? {}) as Record<string, unknown>;
  const s = (r.subject ?? null) as Record<string, unknown> | null;
  const lm = (r.last_message ?? null) as Record<string, unknown> | null;
  const subject: ChatSubject | null = s
    ? {
        listing_id: Number(s.listing_id ?? s.id ?? 0),
        make: (s.make as string) ?? null,
        model: (s.model as string) ?? null,
        year: s.year != null ? Number(s.year) : null,
        price: s.price != null ? Number(s.price) : null,
        currency: (s.currency as string) ?? null,
        image: (s.image as string) ?? null,
      }
    : null;
  return {
    id: String(r.id ?? ""),
    type: r.type === "deal" ? "deal" : "seller",
    title: String(r.title ?? ""),
    status: r.status === "closed" ? "closed" : "active",
    unread: Number(r.unread ?? 0) || 0,
    last_message: lm
      ? {
          body: String(lm.body ?? ""),
          created_at: String(lm.created_at ?? ""),
          sender_type: String(lm.sender_type ?? lm.sender ?? "user") as ChatMessage["sender_type"],
        }
      : null,
    subject,
    lead_id: r.lead_id != null ? Number(r.lead_id) : null,
    lead_status: (r.lead_status as string | null) ?? null,
  };
}

/** GET /api/partner/chats — seller/deal-треды счёта (unread по участнику-партнёру). */
export function fetchPartnerChats(): Promise<PartnerThread[]> {
  return papi<unknown>("/chats").then((raw) =>
    (Array.isArray(raw) ? raw : []).map(toPartnerThread),
  );
}

/** Страница сообщений — тот же контракт, что у ядра F1 (chatSocket.MessagesPage). */
export interface PartnerMessagesPage {
  items: ChatMessage[];
  has_more: boolean;
}

/**
 * GET /api/partner/chats/{id}/messages?before_id=|after_id=&limit=50.
 * Бэк chat-сервиса отдаёт плоский list[MessageOut] (§5); ядро F1 ждёт
 * {items, has_more} — поддерживаем обе формы: у списка has_more считаем
 * по полноте страницы (вернули limit — вероятно, есть ещё).
 */
export function fetchPartnerMessages(
  threadId: string,
  opts: { beforeId?: number; afterId?: number; limit?: number } = {},
): Promise<PartnerMessagesPage> {
  const limit = opts.limit ?? 50;
  const p = new URLSearchParams();
  if (opts.beforeId != null) p.set("before_id", String(opts.beforeId));
  if (opts.afterId != null) p.set("after_id", String(opts.afterId));
  p.set("limit", String(limit));
  return papi<unknown>(`/chats/${threadId}/messages?${p.toString()}`).then((raw) => {
    if (Array.isArray(raw)) return { items: raw as ChatMessage[], has_more: raw.length >= limit };
    const page = (raw ?? {}) as { items?: unknown; has_more?: unknown };
    return {
      items: Array.isArray(page.items) ? (page.items as ChatMessage[]) : [],
      has_more: page.has_more === true,
    };
  });
}

/**
 * POST /api/partner/chats/{id}/messages {body} — markdown ≤4000 → MessageView.
 * Idempotency-Key (долг №7): uuid на вызов — повтор от прокси с тем же
 * заголовком вернёт сохранённое сообщение, а не дубликат.
 */
export function sendPartnerMessage(threadId: string, body: string): Promise<ChatMessage> {
  return papi<ChatMessage>(`/chats/${threadId}/messages`, {
    method: "POST",
    headers: { "Idempotency-Key": newIdempotencyKey() },
    body: JSON.stringify({ body }),
  });
}

/** POST /api/partner/chats/{id}/read {last_read_message_id} — прочитать до id. */
export async function markPartnerRead(threadId: string, lastReadMessageId: number): Promise<void> {
  await papi(`/chats/${threadId}/read`, {
    method: "POST",
    body: JSON.stringify({ last_read_message_id: lastReadMessageId }),
  });
}

/** GET /api/partner/chats/ws-token → {token, url}; JWT sub=partner:{id} (§4). */
export function fetchPartnerWsToken(): Promise<{ token: string; url: string }> {
  return papi<{ token: string; url: string }>("/chats/ws-token");
}

// --- Выжимка диалога (12-ai-trust.md §3) ---

/** Ответ POST /api/partner/chats/{id}/summary — LLM-выжимка диалога
 *  (≤600 знаков: что хочет клиент, лоты, сомнения, следующий шаг). */
export interface PartnerChatSummary {
  summary: string;
  /** Выжимка покрывает сообщения до этого id включительно. */
  as_of_message_id: number;
}

/** POST /api/partner/chats/{id}/summary — выжимка последних ≤50 сообщений
 *  (X-API-Key; кэш бэка 1ч по last_message_id — повторный клик дёшев). */
export function fetchPartnerChatSummary(threadId: string): Promise<PartnerChatSummary> {
  return papi<PartnerChatSummary>(`/chats/${threadId}/summary`, { method: "POST" });
}

/**
 * OG-превью ссылки в партнёрском чате: зеркало user-эндпоинта
 * GET /api/chats/{id}/link-preview под партнёрским префиксом (X-API-Key).
 * Кэш/SSRF — на бэке; здесь любая ошибка → null → плейсхолдер-чип.
 */
export const fetchPartnerLinkPreview: LinkPreviewFetcher = (threadId, url) =>
  papi<LinkPreviewData>(
    `/chats/${threadId}/link-preview?url=${encodeURIComponent(url)}`,
  ).catch(() => null);

// --- Витрина внутреннего рынка (05 §1/§5: partner_offers) ---

/**
 * Мой оффер витрины: карточка лота (routers/market.py MarketOfferOut) +
 * note. created_at бэк пока не отдаёт — дата рисуется по наличию.
 */
export interface PartnerOffer {
  listing_id: number;
  note: string | null;
  created_at?: string | null;
  make: string | null;
  model: string | null;
  year: number | null;
  price: number | null;
  currency: string | null;
  url: string | null;
  image: string | null;
}

/** Нормализация MarketOfferView → PartnerOffer (лишние поля отбрасываем). */
export function toPartnerOffer(raw: unknown): PartnerOffer {
  const r = (raw ?? {}) as Record<string, unknown>;
  const num = (v: unknown): number | null => {
    const n = Number(v);
    return Number.isFinite(n) && n !== 0 ? n : null;
  };
  return {
    listing_id: Number(r.listing_id ?? r.id ?? 0) || 0,
    note: (r.note as string | null) ?? null,
    created_at: (r.created_at as string | null) ?? null,
    make: (r.make as string) ?? null,
    model: (r.model as string) ?? null,
    year: r.year != null ? Number(r.year) : null,
    price: num(r.price),
    currency: (r.currency as string) ?? null,
    url: (r.url as string) ?? null,
    image: (r.image as string) ?? null,
  };
}

/** GET /api/partner/offers — мои лоты в витрине (+ note). */
export function fetchPartnerOffers(): Promise<PartnerOffer[]> {
  return papi<unknown>("/offers").then((raw) =>
    (Array.isArray(raw) ? raw : []).map(toPartnerOffer),
  );
}

/**
 * POST /api/partner/offers {listing_id, note} — поставить лот в витрину:
 * 201 — новый оффер, 200 — reuse (заметка обновлена), чужой лот — 409.
 */
export function addPartnerOffer(
  listingId: number,
  note: string | null,
): Promise<{ listing_id: number }> {
  return papi<{ listing_id: number }>("/offers", {
    method: "POST",
    body: JSON.stringify({ listing_id: listingId, note }),
  });
}

/** DELETE /api/partner/offers/{listing_id} — снять свой лот с витрины. */
export async function removePartnerOffer(listingId: number): Promise<void> {
  await papi(`/offers/${listingId}`, { method: "DELETE" });
}

// --- Объявления рынка (06-market-user-listings.md §2: размещения за слоты) ---

/** GET /api/partner/market/packages — каталог пакетов размещений. */
export function fetchMarketPackages(): Promise<MarketPackage[]> {
  return papi<MarketPackage[]>("/market/packages");
}

/** POST /api/partner/market/packages {size} — покупка со баланса
 *  (PackageOut): size, price_rub и slots_free — свободные места после
 *  покупки (кабинет сразу показывает остаток); 402 — не хватает денег. */
export function buyMarketPackage(
  size: number,
): Promise<{ size: number; price_rub: number; slots_free: number | null }> {
  return papi<{ size: number; price_rub: number; slots_free: number | null }>(
    "/market/packages",
    {
      method: "POST",
      body: JSON.stringify({ size }),
    },
  );
}

/** GET /api/partner/market/lots — мои размещённые лоты (placement 'slot'). */
export function fetchPartnerLots(): Promise<MyLot[]> {
  return papi<MyLot[]>("/market/lots");
}

/** Тело POST /api/partner/market/lots — те же поля, что у user-размещения
 *  (описание ≤2000, фото ≤10 — опциональны). */
export interface PartnerLotBody {
  make: string;
  model: string;
  year: number;
  price: number;
  currency: "KRW" | "USD" | "RUB";
  mileage_km?: number;
  fuel?: string;
  transmission?: string;
  description?: string;
  photos?: string[];
}

/** POST /api/partner/market/lots — разместить лот за слот пакета
 *  (CreatedLotOut); 409 placement_slots_exhausted — свободных слотов нет. */
export function addPartnerLot(body: PartnerLotBody): Promise<CreatedLot> {
  return papi<CreatedLot>("/market/lots", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** DELETE /api/partner/market/lots/{id} — снять размещение (слот не
 *  возвращается: расходуется на публикацию, §1). */
export async function removePartnerLot(lotId: number): Promise<void> {
  await papi(`/market/lots/${lotId}`, { method: "DELETE" });
}

// --- Чеки и история машины (07-sell-chats-partner-inspection.md §3, F2) ---
//
// Словари осмотра скопированы константами с бэка
// app/transport/http/schemas/listing.py (BODY_ZONES / MARK_TYPES /
// ITEM_KEYS / ITEM_STATUSES / GRADES) — партнёрский фронт не тянет
// lib/inspection мини-аппа (файлы партнёрского кабинета самодостаточны).

/** Зоны кузова диаграмм Encar (BODY_ZONES). */
export const INSPECTION_BODY_ZONES = [
  "front_bumper", "hood", "roof", "trunk",
  "fender_fl", "fender_fr", "fender_rl", "fender_rr",
  "door_fl", "door_fr", "door_rl", "door_rr",
  "rocker_l", "rocker_r",
  // Силовая структура (주요골격 классического чека)
  "frame_front", "frame_inside_l", "frame_inside_r",
  "wheelhouse_fl", "wheelhouse_fr", "wheelhouse_rl", "wheelhouse_rr",
  "frame_floor", "frame_rear",
  "pillar_a_l", "pillar_b_l", "pillar_c_l",
  "pillar_a_r", "pillar_b_r", "pillar_c_r",
] as const;

export type PartnerBodyZone = (typeof INSPECTION_BODY_ZONES)[number];

/** Типы повреждений на карте (MARK_TYPES). */
export const INSPECTION_MARK_TYPES = [
  "scratch", "dent", "repaint", "replace", "rust", "crack", "glass",
  "repair", // 2-й ранг внешней панели: был ремонт/окраска
  "frame", // дефект силовой структуры
] as const;

export type PartnerMarkType = (typeof INSPECTION_MARK_TYPES)[number];

/** Агрегаты чек-листа (ITEM_KEYS). */
export const INSPECTION_ITEM_KEYS = [
  "engine", "transmission", "suspension", "brakes", "steering",
  "electronics", "interior", "optics", "glass", "tires", "ac", "leaks",
] as const;

export type PartnerItemKey = (typeof INSPECTION_ITEM_KEYS)[number];

/** Статусы агрегатов чек-листа (ITEM_STATUSES). */
export const INSPECTION_ITEM_STATUSES = ["ok", "warn", "defect", "na"] as const;

export type PartnerItemStatus = (typeof INSPECTION_ITEM_STATUSES)[number];

/** Итоговые оценки чека (GRADES). */
export const INSPECTION_GRADES = ["excellent", "good", "fair", "poor"] as const;

export type PartnerGrade = (typeof INSPECTION_GRADES)[number];

/** Отметка повреждения: зона + тип + заметка; pos — px на диаграмме 254x282. */
export interface PartnerInspectionMark {
  zone: PartnerBodyZone;
  type: PartnerMarkType;
  note?: string | null;
  pos?: [number, number] | null;
}

/** Пункт чек-листа: агрегат + статус + опц. заметка (≤300). */
export interface PartnerInspectionItem {
  key: PartnerItemKey;
  status: PartnerItemStatus;
  note?: string | null;
}

/** InspectionReportOut (GET /api/partner/listings/{id}/inspection). */
export interface PartnerInspectionReport {
  source: string;
  inspector?: string | null;
  /** ISO-дата/дата-время осмотра. */
  inspected_at?: string | null;
  grade?: PartnerGrade | null;
  summary?: string | null;
  marks: PartnerInspectionMark[];
  items: PartnerInspectionItem[];
  images: string[];
}

/** Тело PUT …/inspection (InspectionReportIn; source бэк форсит 'partner'). */
export interface PartnerInspectionBody {
  inspector?: string | null;
  /** YYYY-MM-DD (pydantic datetime принимает дату как полночь). */
  inspected_at?: string | null;
  grade?: PartnerGrade | null;
  summary?: string | null;
  marks: PartnerInspectionMark[];
  items: PartnerInspectionItem[];
  images: string[];
}

/** Событие истории машины (OwnerHistoryEventIn): date ≤120 title, note ≤300. */
export interface PartnerHistoryEvent {
  /** YYYY-MM-DD. */
  date: string;
  title: string;
  note?: string | null;
}

/**
 * GET /api/partner/listings/{id}/inspection → чек своего лота.
 * 404 = чека нет (или лот не свой — бэк неотличим) → null, остальные
 * ошибки пробрасываются (маппинг §7.9 наверху).
 */
export async function fetchPartnerInspection(
  listingId: number,
): Promise<PartnerInspectionReport | null> {
  try {
    return await papi<PartnerInspectionReport>(`/listings/${listingId}/inspection`);
  } catch (e) {
    if (e instanceof PartnerApiError && e.status === 404) return null;
    throw e;
  }
}

/** PUT /api/partner/listings/{id}/inspection — создать/заменить чек. */
export function putPartnerInspection(
  listingId: number,
  body: PartnerInspectionBody,
): Promise<PartnerInspectionReport> {
  return papi<PartnerInspectionReport>(`/listings/${listingId}/inspection`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

/** PUT /api/partner/listings/{id}/history — заменить историю (≤50 событий). */
export async function putPartnerHistory(
  listingId: number,
  events: PartnerHistoryEvent[],
): Promise<void> {
  await papi(`/listings/${listingId}/history`, {
    method: "PUT",
    body: JSON.stringify({ events }),
  });
}

/**
 * История машины для редактора: чтения под X-API-Key нет — события
 * отдаёт публичная карточка (GET /api/listings/{id} → ListingOut.
 * owner_history). Любая ошибка → [] (редактор начинается с пустого
 * таймлайна, потерять нечего).
 */
export async function fetchListingOwnerHistory(
  listingId: number,
): Promise<PartnerHistoryEvent[]> {
  try {
    const resp = await fetch(`/api/listings/${listingId}`);
    if (!resp.ok) return [];
    const body = (await resp.json().catch(() => null)) as {
      owner_history?: unknown;
    } | null;
    if (!Array.isArray(body?.owner_history)) return [];
    return body.owner_history
      .map((raw) => {
        const r = (raw ?? {}) as Record<string, unknown>;
        return {
          date: String(r.date ?? ""),
          title: String(r.title ?? ""),
          note: (r.note as string | null) ?? null,
        };
      })
      .filter((e) => /^\d{4}-\d{2}-\d{2}$/.test(e.date));
  } catch {
    return [];
  }
}

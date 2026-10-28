/** Типы API (контракты backend — app/schemas). */

export interface User {
  telegram_id: number;
  username: string | null;
  first_name: string | null;
  last_name: string | null;
  language_code: string | null;
  role: "buyer" | "dealer" | "salon" | "admin";
  subscription_tier: "free" | "pro" | "business";
  subscription_until: string | null;
  created_at: string;
  is_active: boolean;
  onboarded_at: string | null;
}

export interface AuthResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: User;
}

export interface Criteria {
  make?: string;
  model?: string;
  year_from?: number;
  year_to?: number;
  price_min?: number;
  price_max?: number;
  currency?: string;
  mileage_max?: number;
  fuel?: string;
  transmission?: string;
  country?: string;
  sources?: string[];
}

export interface UserFilter {
  id: number;
  name: string | null;
  criteria: Criteria;
  notify_mode: "instant" | "digest" | "off";
  quiet_hours: string | null;
  created_at: string;
  updated_at: string | null;
}

/** Точка истории цен (GET /api/listings/{id}, PLAN.md фаза 4). */
export interface PricePoint {
  price: number;
  currency: string | null;
  recorded_at: string;
}

export interface Listing {
  id: number;
  source: string;
  source_id: string;
  url: string;
  make: string | null;
  model: string | null;
  year: number | null;
  price: number | null;
  currency: string | null;
  mileage_km: number | null;
  vin: string | null;
  /** Спеки (миграция 0016): кузов/топливо/КПП/привод — строка в карточке. */
  body_type?: string | null;
  fuel?: string | null;
  transmission?: string | null;
  drive?: string | null;
  /** Объём (литры) и цвет — миграция 0017, enrichment из detail encar. */
  engine_volume?: number | null;
  color?: string | null;
  images: string[];
  /** Комплектация (миграция 0023): русифицированные опции источника. */
  options?: string[];
  parsed_at: string;
  /** Отслеживание (PLAN.md, фаза 4) */
  is_active: boolean;
  first_seen_at: string | null;
  /** Первая точка истории цен — для бейджа «цена снизилась» (избранное) */
  first_price: number | null;
  /** Заполнена только в карточке GET /api/listings/{id} */
  price_history: PricePoint[] | null;
  /** Перевод тайтла (PLAN.md, фаза 5): оригинал с источника + перевод (NLLB) */
  title_raw: string | null;
  title_ru: string | null;
  source_language?: string | null;
  /** Полная хронология (карточка) */
  updated_at?: string | null;
  last_seen_at?: string | null;
  price_changed_at?: string | null;
  /** Рынок владельцев (06-market-user-listings §4): 'user'/'partner' —
   *  лот рынка с продавцом (seller-чат доступен), null — внешний лот
   *  (encar/kcar, чата с продавцом нет). Поле появляется после B1. */
  owner_type?: "user" | "partner" | null;
  /** Описание от владельца лота рынка (≤2000) — блок «От владельца» на PDP. */
  owner_description?: string | null;
  /** История машины (07 §3, партнёрские лоты): события «как в Корее» —
   *  [{date, title, note?}] ≤50, свежие первыми — таймлайн на PDP. */
  owner_history?: { date: string; title: string; note?: string | null }[];
}

export interface ListingList {
  total: number;
  items: Listing[];
}

/** GET /api/subscription (этап 6, ТЗ п. 8.7, 10.2; прод-ревизия 2026-10:
 *  digest/inspection/agent/analyses_* — новая экономика тарифов). */
export interface Subscription {
  tier: "free" | "pro" | "business";
  paid_tier: string;
  subscription_until: string | null;
  max_filters: number | null;
  ai_daily_limit: number | null;
  instant: boolean;
  prices: Record<string, number>;
  /** Каденс дайджеста: weekly (free) / daily (pro) / instant (business). */
  digest: "weekly" | "daily" | "instant";
  /** Данные чеков осмотра доступны (pro/business). */
  inspection: boolean;
  /** Отправка агента на сделку (business). */
  agent: boolean;
  /** Лимит AI-анализов на окно; null = безлимит. */
  analyses_limit: number | null;
  /** Размер окна лимита в днях (free — 7, pro — 1). */
  analyses_window_days: number;
}

/** GET /api/me/analyses — история «Мои анализы» (AI-разборы цены). */
export interface MyAnalysis {
  id: number;
  listing_id: number;
  verdict: "deal" | "fair" | "overpriced";
  fair_min: number | null;
  fair_max: number | null;
  summary: string | null;
  created_at: string;
  make: string | null;
  model: string | null;
  year: number | null;
  price: number | null;
  currency: string | null;
  image: string | null;
}

/** POST /api/listings/{id}/similar — похожая машина из реранга (LLM). */
export interface SimilarItem {
  id: number;
  make: string | null;
  model: string | null;
  year: number | null;
  price: number | null;
  currency: string | null;
  mileage_km: number | null;
  image: string | null;
  url: string;
  /** Почему машина похожа/чем интересна — текст реранкера. */
  reason: string | null;
  score: number | null;
}

/** Ответ POST /api/listings/{id}/similar. */
export interface SimilarResponse {
  source: "ai" | "stats";
  items: SimilarItem[];
}

/** POST /api/payments/stars (ТЗ п. 9.6). */
export interface StarsInvoice {
  payment_id: number;
  invoice_url: string;
  amount: number;
  currency: string;
  tier: string;
  period_days: number;
}

/** POST /api/listings/{id}/contact — заявка «Связаться с менеджером»
 *  (монетизация/сведение с партнёрами; доступна всем авторизованным). */
export interface ContactResponse {
  lead_id: number;
  status: string;
}

// ---------- ИИ доверия (12-ai-trust.md §1–§2) ----------

/** GET /api/listings/{id}/history-report (12 §1): факты истории лота +
 *  LLM-вывод. facts — плоский словарь (accidents_count, last_accident,
 *  owners, mileage_confirmed, usage, report_grade, report_date); 404 с
 *  detail no_history_data — данных по истории нет; гость — 403. */
export interface HistoryReportView {
  listing_id: number;
  facts: Record<string, string | number>;
  /** LLM-абзац-вывод «стоит ли смотреть»; null — только структурная часть. */
  llm_summary: string | null;
  generated_at: string;
}

/** «Качество объявления» (12 §2): чистая функция бэка listing_quality —
 *  score 0-100 + советы; null/отсутствие поля — не посчитано (старый
 *  контракт до B12). Приходит в my-lots и ответах create/update лота. */
export interface ListingQuality {
  score: number;
  /** Человекочитаемые советы (пусто — всё в порядке). */
  tips: string[];
}

// ---------- Рынок владельцев (06-market-user-listings.md §2) ----------

/** Жизненный цикл объявления владельца (10-listing-lifecycle.md §1):
 *  draft → moderation → accepted/rejected → archived/expired.
 *  Отсутствие поля — старый контракт (внешние encar/kcar-лоты всегда
 *  видны, партнёрские — status в ответе как раньше). */
export type LotStatus = "draft" | "moderation" | "accepted" | "rejected" | "archived" | "expired";

/** Сработавшее правило авто-модерации (moderation_payload.violations). */
export interface LotViolation {
  rule: string;
  detail: string;
}

/** GET /api/market/my-lots (и /api/partner/market/lots) — лот с
 *  placement-статусом, контракт MyLotOut бэка. placement_status:
 *  включён (Pro/Business, без дат) / бесплатный период / оплачен
 *  звёздами / истёк (снят экспиратором) / ждёт оплаты после создания;
 *  'slot' — партнёрские лоты (размещение за слот пакета). */
export interface MyLot {
  listing_id: number;
  /** Статус жизненного цикла (10 §1); undefined — поле ещё нет в ответе. */
  status?: LotStatus;
  /** Причина отклонения модерацией (для rejected). */
  rejected_reason?: string | null;
  /** Сработавшие правила авто-модерации (для rejected/ручной очереди). */
  violations?: LotViolation[] | null;
  /** Версия на проверке при редактировании accepted-лота (10 §1):
   *  NOT NULL = правки на модерации; patch_reject_reason — причина
   *  отклонения предыдущих правок. */
  pending_patch?: { patch_reject_reason?: string | null } | null;
  placement_status: "included" | "free" | "paid" | "expired" | "needs_payment" | "slot";
  is_active: boolean;
  /** Конец бесплатного периода (user, §3). */
  placement_free_until?: string | null;
  /** Оплачено до (user, Stars-продления). */
  placement_paid_until?: string | null;
  /** max(free, paid); null — бессрочный (included/slot). */
  placement_deadline?: string | null;
  make: string | null;
  model: string | null;
  year: number | null;
  price: number | null;
  currency: string | null;
  mileage_km: number | null;
  images: string[];
  url: string | null;
  owner_description: string | null;
  /** Спеки для предзаполнения edit-формы (/sell?edit): бэк может отдать
   *  поля лота — если нет, селекты остаются «Не выбрано». */
  fuel?: string | null;
  transmission?: string | null;
  /** Качество объявления (12 §2): score 0-100 + советы; null/нет —
   *  бэк ещё не считает качество (старый контракт). */
  quality?: ListingQuality | null;
}

/** POST /api/market/lots и /api/partner/market/lots (CreatedLotOut):
 *  needs_payment=true — лот опубликован после Stars-оплаты (§2-§3).
 *  submit=true (10 §4) сразу гонит модерацию — status в ответе может
 *  быть moderation (вручную) / accepted / rejected с причиной. */
export interface CreatedLot {
  listing_id: number;
  status?: LotStatus;
  rejected_reason?: string | null;
  violations?: LotViolation[] | null;
  placement_status: MyLot["placement_status"];
  is_active: boolean;
  needs_payment?: boolean;
  /** Качество объявления (12 §2) — считается при создании/правке лота. */
  quality?: ListingQuality | null;
}

/** POST /api/market/lots/{id}/submit (10 §4) — синхронный авто-вердикт:
 *  draft/rejected → moderation → результат (новый статус) сразу. */
export interface LotSubmitResult {
  status: LotStatus;
  rejected_reason?: string | null;
  violations?: LotViolation[] | null;
}

/** GET /api/partner/market/packages — пакеты размещений партнёра. */
export interface MarketPackage {
  /** Число мест в пакете (10/50/200, конфиг market_partner_packages). */
  size: number;
  /** Цена пакета в рублях (списание с баланса счёта). */
  price_rub: number;
}

/** POST /api/market/lots/{id}/extend {weeks:1|4} — Stars-инвойс
 *  размещения (kind='placement', 20★/нед; оплата как подписки в Profile). */
export interface PlacementInvoice {
  payment_id: number;
  invoice_url: string;
  amount: number;
  currency: "XTR";
}

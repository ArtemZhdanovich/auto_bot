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
  images: string[];
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
}

export interface ListingList {
  total: number;
  items: Listing[];
}

/** GET /api/subscription (этап 6, ТЗ п. 8.7, 10.2). */
export interface Subscription {
  tier: "free" | "pro" | "business";
  paid_tier: string;
  subscription_until: string | null;
  max_filters: number | null;
  ai_daily_limit: number | null;
  instant: boolean;
  prices: Record<string, number>;
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

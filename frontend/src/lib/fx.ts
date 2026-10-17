/**
 * Общие чистые хелперы миниаппа: фикс-курсы MVP (Корея) и парсеры.
 * Курсы — константы MVP; потом заменить на ЦБ (один источник бот+веб).
 */

export const KRW_PER_USD = 1350;
export const EUR_PER_USD = 1.08;

/** Локальные валюты (фикс-курсы MVP; потом ЦБ — один источник бот+веб). */
export const RUB_PER_USD = 92;
export const BYN_PER_USD = 3.05;

/** Регион пользователя: валюта и дисклеймеры расчёта (РБ/РФ). */
export type Region = "ru" | "by";

const REGION_KEY = "autopick_region";

export function getRegion(): Region {
  try {
    const saved = localStorage.getItem(REGION_KEY);
    if (saved === "ru" || saved === "by") return saved;
  } catch {
    /* private mode */
  }
  const lang = navigator.language || "";
  return lang.toLowerCase().includes("-by") ? "by" : "ru";
}

export function setRegion(r: Region): void {
  try {
    localStorage.setItem(REGION_KEY, r);
  } catch {
    /* private mode */
  }
}

export const REGION_LABEL: Record<Region, string> = { ru: "🇷🇺 Россия", by: "🇧🇾 Беларусь" };
export const REGION_CURRENCY: Record<Region, string> = { ru: "₽", by: "Br" };

/** Локальная валюта: «1 656 000 ₽» / «54 900 Br» (округление до сотен). */
export function formatLocal(usd: number, region: Region): string {
  const cur = REGION_CURRENCY[region];
  const rate = region === "by" ? BYN_PER_USD : RUB_PER_USD;
  const val = Math.round((usd * rate) / 100) * 100;
  return `${val.toLocaleString("ru-RU")} ${cur}`;
}

/** Цена в $: «$18 000». */
export function formatUsd(usd: number): string {
  return `$${usd.toLocaleString("ru-RU")}`;
}

/** Цена источника: «24 300 000 ₩». */
export function formatOriginal(price: number, currency?: string | null): string {
  return `${price.toLocaleString("ru-RU")} ${currency ?? ""}`.trim();
}

/**
 * Цепочка цен: сначала $, потом локальная валюта региона, потом источник.
 * «$18 000 · 1 656 000 ₽ · 24 300 000 ₩» (источник опускается, если USD).
 */
export function priceChain(
  price: number | null | undefined,
  currency: string | null | undefined,
  region: Region,
  { withOriginal = true }: { withOriginal?: boolean } = {},
): { usd: string | null; local: string | null; original: string | null } {
  const usd = toUsd(price, currency);
  if (usd == null) {
    const orig = price && price > 0 ? formatOriginal(price, currency) : null;
    return { usd: null, local: null, original: orig };
  }
  const local = formatLocal(usd, region);
  const orig =
    withOriginal && (currency ?? "USD").toUpperCase() !== "USD" && price
      ? formatOriginal(price, currency)
      : null;
  return { usd: formatUsd(usd), local, original: orig };
}

/** Цена в $: KRW ÷1350, EUR ×1.08, RUB ÷92, BYN ÷3.05, USD как есть; null для пустых/нулевых. */
export function toUsd(
  price: number | null | undefined,
  currency?: string | null,
): number | null {
  if (price == null || price <= 0) return null;
  const cur = (currency ?? "USD").toUpperCase();
  if (cur === "KRW") return Math.round(price / KRW_PER_USD);
  if (cur === "EUR") return Math.round(price * EUR_PER_USD);
  if (cur === "RUB") return Math.round(price / RUB_PER_USD);
  if (cur === "BYN") return Math.round(price / BYN_PER_USD);
  return Math.round(price);
}

/**
 * Регион для расчёта растаможки из валюты объявления:
 * BYN → Беларусь (сборы/утильсбор РБ), RUB → РФ, остальное → регион пользователя.
 */
export function regionFromCurrency(currency: string | null | undefined, fallback: Region): Region {
  const cur = (currency ?? "").toUpperCase();
  if (cur === "BYN") return "by";
  if (cur === "RUB") return "ru";
  return fallback;
}

/** Вариант для экрана сравнения: строки-цифры из инпутов. */
export interface Variant {
  price: string;
  cc: string;
  year: string;
}

/** Только цифры из строки (для inputMode="numeric" инпутов). */
export const digits = (s: string) => s.replace(/\D/g, "");

/** Парсинг deep-link ?v=18000x2000x2022,15000x1600x2021 → варианты. */
export function parseQueryVariants(q: string | null): Variant[] {
  if (!q) return [];
  return q
    .split(",")
    .map((raw) => {
      const parts = raw.trim().split("x");
      const price = parts[0] ?? "";
      const cc = parts.length >= 3 ? parts[1] || "2000" : "2000";
      const year = parts.length >= 3 ? parts[2] ?? "" : parts[1] ?? "";
      return { price, cc, year };
    })
    .filter((v) => digits(v.price) && digits(v.year));
}

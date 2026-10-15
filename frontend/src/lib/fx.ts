/**
 * Общие чистые хелперы миниаппа: фикс-курсы MVP (Корея) и парсеры.
 * Курсы — константы MVP; потом заменить на ЦБ (один источник бот+веб).
 */

export const KRW_PER_USD = 1350;
export const EUR_PER_USD = 1.08;

/** Цена в $: KRW ÷1350, EUR ×1.08, USD как есть; null для пустых/нулевых. */
export function toUsd(
  price: number | null | undefined,
  currency?: string | null,
): number | null {
  if (price == null || price <= 0) return null;
  const cur = (currency ?? "USD").toUpperCase();
  if (cur === "KRW") return Math.round(price / KRW_PER_USD);
  if (cur === "EUR") return Math.round(price * EUR_PER_USD);
  return Math.round(price);
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

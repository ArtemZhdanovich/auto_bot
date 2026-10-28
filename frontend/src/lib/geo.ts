/**
 * Справочник стран (GEO-V1 §2–§3): listings.country — ISO-3166 alpha-2,
 * на фронте нужен флаг-эмодзи + русское имя для бейджей карточки, строки
 * характеристик PDP, селекта страны в SellCar и сегмента калькулятора.
 *
 * Единый словарь — чтобы «KR» в ленте, «🇰🇷 Корея» в шите и «Корея» в
 * калькуляторе не разъехались; неизвестный код (новый источник до
 * бэкфилла) рендерится как есть, без имени.
 */

export interface CountryInfo {
  /** ISO-3166 alpha-2 (контракт ListingOut.country). */
  code: string;
  /** Русское имя для подписей («Страна: ОАЭ»). */
  name: string;
  /** Эмодзи-флаг (из региональных символов). */
  flag: string;
}

/** Все известные фронту страны: источники (KR/CN/AE/US/KZ) + рынок
 *  (RU и соседи ЕАЭС/Кавказа, куда реально возят и продают). */
export const COUNTRIES: CountryInfo[] = [
  // источники импорта (лента/калькулятор)
  { code: "KR", name: "Корея", flag: "🇰🇷" },
  { code: "CN", name: "Китай", flag: "🇨🇳" },
  { code: "AE", name: "ОАЭ", flag: "🇦🇪" },
  { code: "US", name: "США", flag: "🇺🇸" },
  { code: "KZ", name: "Казахстан", flag: "🇰🇿" },
  // рынок владельцев (SellCar: дефолт RU — внутренний рынок)
  { code: "RU", name: "Россия", flag: "🇷🇺" },
  { code: "BY", name: "Беларусь", flag: "🇧🇾" },
  { code: "GE", name: "Грузия", flag: "🇬🇪" },
  { code: "AM", name: "Армения", flag: "🇦🇲" },
  { code: "KG", name: "Кыргызстан", flag: "🇰🇬" },
  { code: "UZ", name: "Узбекистан", flag: "🇺🇿" },
  { code: "AZ", name: "Азербайджан", flag: "🇦🇿" },
  { code: "TR", name: "Турция", flag: "🇹🇷" },
  { code: "JP", name: "Япония", flag: "🇯🇵" },
];

/** Быстрый lookup по коду. */
export const COUNTRY_BY_CODE: Record<string, CountryInfo> = Object.fromEntries(
  COUNTRIES.map((c) => [c.code, c]),
);

/** Быстрые чипсы-страны ленты и сегмент калькулятора (§3): пять источников. */
export const FEED_COUNTRIES: CountryInfo[] = [
  COUNTRY_BY_CODE.KR,
  COUNTRY_BY_CODE.CN,
  COUNTRY_BY_CODE.AE,
  COUNTRY_BY_CODE.US,
  COUNTRY_BY_CODE.KZ,
];

/** Страны рынка для SellCar (§3): RU по умолчанию (внутренний рынок),
 *  соседи — рядом, импортные источники (KR/CN/AE/US) не предлагаются. */
export const SELL_COUNTRIES: CountryInfo[] = [
  COUNTRY_BY_CODE.RU,
  COUNTRY_BY_CODE.KZ,
  COUNTRY_BY_CODE.GE,
  COUNTRY_BY_CODE.AM,
  COUNTRY_BY_CODE.BY,
  ...COUNTRIES.filter(
    (c) => !["RU", "KZ", "GE", "AM", "BY", "KR", "CN", "AE", "US"].includes(c.code),
  ),
];

/** «🇰🇷 Корея» — флаг + имя; неизвестный код → он сам (без имени). */
export function countryLabel(code: string | null | undefined): string {
  if (!code) return "";
  const c = COUNTRY_BY_CODE[code.toUpperCase()];
  return c ? `${c.flag} ${c.name}` : code;
}

/** Только флаг; неизвестный код → нейтральный белый флаг. */
export function countryFlag(code: string | null | undefined): string {
  if (!code) return "";
  return COUNTRY_BY_CODE[code.toUpperCase()]?.flag ?? "🏳️";
}

/** Русское имя страны без флага; неизвестный код → «». */
export function countryName(code: string | null | undefined): string {
  if (!code) return "";
  return COUNTRY_BY_CODE[code.toUpperCase()]?.name ?? "";
}

/** Справочники спеков для фильтров и карточек (единообразие Feed/Filters). */

export const FUELS: Array<[string, string]> = [
  ["", "Любое топливо"],
  ["petrol", "Бензин"],
  ["diesel", "Дизель"],
  ["hybrid", "Гибрид"],
  ["electric", "Электро"],
  ["lpg", "Газ / LPG"],
];

export const TRANSMISSIONS: Array<[string, string]> = [
  ["", "Любая КПП"],
  ["auto", "Автомат"],
  ["manual", "Механика"],
  ["cvt", "Вариатор"],
  ["dct", "Робот / DCT"],
];

export const BODIES: Array<[string, string]> = [
  ["", "Любой кузов"],
  ["sedan", "Седан"],
  ["hatchback", "Хэтчбек"],
  ["wagon", "Универсал"],
  ["suv", "Кроссовер / SUV"],
  ["coupe", "Купе"],
  ["van", "Минивэн"],
  ["pickup", "Пикап"],
];

export const DRIVES: Array<[string, string]> = [
  ["", "Любой привод"],
  ["fwd", "Передний"],
  ["rwd", "Задний"],
  ["awd", "Полный"],
];

/** Подпись значения справочника: канон и сырые значения источников. */
function labelOf(dict: Array<[string, string]>, value: string | null | undefined): string | null {
  if (!value) return null;
  const v = value.toLowerCase();
  const hit = dict.find(([key]) => key === v);
  return hit ? hit[1] : value; // нераспознанное сырое значение показываем как есть
}

export const fuelLabel = (v?: string | null) => labelOf(FUELS, v);
export const transmissionLabel = (v?: string | null) => labelOf(TRANSMISSIONS, v);
export const bodyLabel = (v?: string | null) => labelOf(BODIES, v);
export const driveLabel = (v?: string | null) => labelOf(DRIVES, v);

/** Страна источника: пока все лоты — Корея (encar/kcar). */
export function sourceCountry(source: string): string {
  const map: Record<string, string> = { encar: "🇰🇷 Корея", kcar: "🇰🇷 Корея" };
  return map[source.toLowerCase()] ?? source;
}

/**
 * «Качество объявления» (12-ai-trust.md §2, F12): локальная часть правил
 * listing_quality бэка — чистые функции без LLM.
 *
 * В SellCar чек-лист считается live по полям визарда (фото + длина
 * описания). Цену-медиану модели локально не считаем (нет данных рынка
 * на клиенте): авторитетный score с правилом цены приходит с бэка —
 * quality в my-lots / ответе создания лота — и рисуется в «Мои
 * объявления» профиля.
 */

/** Порог «достаточно фото» (§2: фото<3 → совет «добавьте фото салона/кузова»). */
export const QUALITY_MIN_PHOTOS = 3;

/** Порог «описание подробное» (§2: description<80 → «коротко»). */
export const QUALITY_MIN_DESC = 80;

/** Пункт чек-листа качества: ✓ (выполнено) или ⚠ (совет). */
export interface QualityCheck {
  /** Подпись правила в чек-листе (с текущими числами). */
  label: string;
  /** true — правило выполнено (✓), false — совет (⚠). */
  ok: boolean;
  /** Совет-подсказка при !ok. */
  tip: string;
}

/**
 * Live-правила качества в визарде SellCar (шаг 3): число загруженных
 * фото и длина описания — те же пороги, что у бэка (§2).
 */
export function localQualityChecks(photosCount: number, description: string): QualityCheck[] {
  const descLen = description.trim().length;
  return [
    {
      label: `Фото — ${photosCount} из ${QUALITY_MIN_PHOTOS}`,
      ok: photosCount >= QUALITY_MIN_PHOTOS,
      tip: "Добавьте фото салона и кузова — объявления с 3+ фото смотрят чаще",
    },
    {
      label: `Описание — ${descLen} из ${QUALITY_MIN_DESC} символов`,
      ok: descLen >= QUALITY_MIN_DESC,
      tip: "Опишите комплектацию, состояние и историю обслуживания",
    },
  ];
}

/** Тон score-пилюли качества по порогам (как вердикт цены):
 *  ≥80 — deal, ≥50 — warn, ниже — danger. */
export function qualityTone(score: number): "deal" | "warn" | "danger" {
  if (score >= 80) return "deal";
  if (score >= 50) return "warn";
  return "danger";
}

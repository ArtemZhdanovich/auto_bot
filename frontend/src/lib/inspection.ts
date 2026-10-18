/** Словари осмотра (миграция 0018): зоны кузова, типы дефектов, чек-лист.
 *  Ключи синхронизированы с app/schemas/listing.py (BODY_ZONES / MARK_TYPES / ITEM_KEYS). */

export type BodyZone =
  | "front_bumper" | "hood" | "roof" | "trunk"
  | "fender_fl" | "fender_fr" | "fender_rl" | "fender_rr"
  | "door_fl" | "door_fr" | "door_rl" | "door_rr"
  | "rocker_l" | "rocker_r";

export type MarkType =
  | "scratch" | "dent" | "repaint" | "replace" | "rust" | "crack" | "glass";

export type ItemKey =
  | "engine" | "transmission" | "suspension" | "brakes" | "steering"
  | "electronics" | "interior" | "optics" | "glass" | "tires" | "ac" | "leaks";

export type ItemStatus = "ok" | "warn" | "defect" | "na";

export type Grade = "excellent" | "good" | "fair" | "poor";

export interface InspectionMark {
  zone: BodyZone;
  type: MarkType;
  note?: string | null;
}

export interface InspectionItem {
  key: ItemKey;
  status: ItemStatus;
  note?: string | null;
}

export interface InspectionReport {
  source: string;
  inspector?: string | null;
  inspected_at?: string | null;
  grade?: Grade | null;
  summary?: string | null;
  marks: InspectionMark[];
  items: InspectionItem[];
}

/** Порядок важности: чем левее, тем «сильнее» дефект — зона красится им. */
export const MARK_SEVERITY: MarkType[] = [
  "replace", "crack", "dent", "rust", "repaint", "glass", "scratch",
];

export const ZONE_LABEL: Record<BodyZone, string> = {
  front_bumper: "Передний бампер",
  hood: "Капот",
  roof: "Крыша",
  trunk: "Крышка багажника",
  fender_fl: "Крыло переднее, левое",
  fender_fr: "Крыло переднее, правое",
  fender_rl: "Крыло заднее, левое",
  fender_rr: "Крыло заднее, правое",
  door_fl: "Дверь передняя, левая",
  door_fr: "Дверь передняя, правая",
  door_rl: "Дверь задняя, левая",
  door_rr: "Дверь задняя, правая",
  rocker_l: "Порог, левый",
  rocker_r: "Порог, правый",
};

export const MARK_LABEL: Record<MarkType, string> = {
  scratch: "Царапина",
  dent: "Вмятина",
  repaint: "Покраска",
  replace: "Замена элемента",
  rust: "Коррозия",
  crack: "Трещина",
  glass: "Повреждение стекла/оптики",
};

/** Цвет заливки зоны на схеме по типу дефекта. */
export const MARK_COLOR: Record<MarkType, string> = {
  scratch: "#f59e0b",
  dent: "#ef4444",
  repaint: "#8b5cf6",
  replace: "#111827",
  rust: "#b45309",
  crack: "#dc2626",
  glass: "#06b6d4",
};

export const ITEM_LABEL: Record<ItemKey, string> = {
  engine: "Двигатель",
  transmission: "Трансмиссия / КПП",
  suspension: "Подвеска",
  brakes: "Тормозная система",
  steering: "Рулевое управление",
  electronics: "Электроника / приборы",
  interior: "Салон",
  optics: "Оптика",
  glass: "Стёкла",
  tires: "Шины / диски",
  ac: "Кондиционер / климат",
  leaks: "Потёки технических жидкостей",
};

export const STATUS_ICON: Record<ItemStatus, string> = {
  ok: "✅",
  warn: "⚠️",
  defect: "❌",
  na: "➖",
};

export const GRADE_LABEL: Record<Grade, string> = {
  excellent: "🏆 Отличное",
  good: "✅ Хорошее",
  fair: "🟡 Удовлетворительное",
  poor: "🔴 Требует внимания",
};

/** Самый сильный дефект зоны — цвет заливки на схеме. */
export function zoneColor(marks: InspectionMark[], zone: BodyZone): string {
  const zoneMarks = marks.filter((m) => m.zone === zone);
  if (zoneMarks.length === 0) return "transparent";
  for (const t of MARK_SEVERITY) {
    if (zoneMarks.some((m) => m.type === t)) return MARK_COLOR[t];
  }
  return MARK_COLOR[zoneMarks[0].type];
}

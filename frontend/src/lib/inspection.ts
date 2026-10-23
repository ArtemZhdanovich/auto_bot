/** Словари осмотра (миграция 0018): зоны кузова, типы дефектов, чек-лист.
 *  Ключи синхронизированы с app/schemas/listing.py (BODY_ZONES / MARK_TYPES / ITEM_KEYS). */

export type BodyZone =
  | "front_bumper" | "hood" | "roof" | "trunk"
  | "fender_fl" | "fender_fr" | "fender_rl" | "fender_rr"
  | "door_fl" | "door_fr" | "door_rl" | "door_rr"
  | "rocker_l" | "rocker_r"
  // Силовая структура (주요골격 классического чека Encar)
  | "frame_front" | "frame_inside_l" | "frame_inside_r"
  | "wheelhouse_fl" | "wheelhouse_fr" | "wheelhouse_rl" | "wheelhouse_rr"
  | "frame_floor" | "frame_rear"
  | "pillar_a_l" | "pillar_b_l" | "pillar_c_l"
  | "pillar_a_r" | "pillar_b_r" | "pillar_c_r";

export type MarkType =
  | "scratch" | "dent" | "repaint" | "replace" | "rust" | "crack" | "glass"
  | "repair"  // 2-й ранг внешней панели: был ремонт/окраска
  | "frame";  // дефект силовой структуры

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
  images?: string[] | null;
}

/** Порядок важности: чем левее, тем «сильнее» дефект — зона красится им. */
export const MARK_SEVERITY: MarkType[] = [
  "replace", "frame", "crack", "dent", "rust", "repair", "repaint", "glass", "scratch",
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
  frame_front: "Передняя панель / лонжероны",
  frame_inside_l: "Брызговик (апрон), левый",
  frame_inside_r: "Брызговик (апрон), правый",
  wheelhouse_fl: "Стакан переднего колеса, левый",
  wheelhouse_fr: "Стакан переднего колеса, правый",
  wheelhouse_rl: "Арка заднего колеса, левая",
  wheelhouse_rr: "Арка заднего колеса, правая",
  frame_floor: "Пол кузова",
  frame_rear: "Задняя панель / пол багажника",
  pillar_a_l: "Стойка A, левая",
  pillar_b_l: "Стойка B, левая",
  pillar_c_l: "Стойка C, левая",
  pillar_a_r: "Стойка A, правая",
  pillar_b_r: "Стойка B, правая",
  pillar_c_r: "Стойка C, правая",
};

export const MARK_LABEL: Record<MarkType, string> = {
  scratch: "Царапина",
  dent: "Вмятина",
  repaint: "Покраска",
  replace: "Замена элемента",
  rust: "Коррозия",
  crack: "Трещина",
  glass: "Повреждение стекла/оптики",
  repair: "Ремонт / окраска панели",
  frame: "Дефект силовой структуры",
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
  repair: "#f97316",   // оранжевый: внешний ремонт/окраска (2-й ранг)
  frame: "#991b1b",    // тёмно-красный: силовая структура
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

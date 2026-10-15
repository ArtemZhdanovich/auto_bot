/** AI-анализ цены: типы разбора и математика графика (чистые функции). */

export interface AnalysisData {
  verdict: "deal" | "fair" | "overpriced";
  price: number;
  currency: string;
  fair_min: number | null;
  fair_max: number | null;
  summary: string | null;
  factors: string[];
  comps: { count: number; min: number; max: number; median: number } | null;
  history: { price: number; recorded_at: string }[];
  source: string;
  created_at?: string | null;
}

export const VERDICT_LABEL: Record<AnalysisData["verdict"], { text: string; emoji: string }> = {
  deal: { text: "Выгодная цена", emoji: "🟢" },
  fair: { text: "Справедливая цена", emoji: "🟡" },
  overpriced: { text: "Дорого", emoji: "🔴" },
};

/** Отклонение цены от середины справедливого диапазона, %. */
export function deltaPct(a: Pick<AnalysisData, "price" | "fair_min" | "fair_max">): number | null {
  if (!a.fair_min || !a.fair_max) return null;
  const mid = (a.fair_min + a.fair_max) / 2;
  if (!mid) return null;
  return Math.round(((a.price - mid) / mid) * 100);
}

/** Где цена внутри справедливого диапазона, 0..100 % (для маркера на шкале). */
export function fairPosition(
  a: Pick<AnalysisData, "price" | "fair_min" | "fair_max">,
): number | null {
  if (!a.fair_min || !a.fair_max || a.fair_max <= a.fair_min) return null;
  const t = (a.price - a.fair_min) / (a.fair_max - a.fair_min);
  return Math.round(Math.min(1, Math.max(0, t)) * 100);
}

/** Компактный формат для осей/подписей: 18_400_000 → «18,4 млн», 950_000 → «950 тыс». */
export function formatCompact(v: number): string {
  const abs = Math.abs(v);
  if (abs >= 1_000_000) {
    const m = (v / 1_000_000).toFixed(1).replace(".", ",").replace(",0", "");
    return `${m} млн`;
  }
  if (abs >= 1_000) return `${Math.round(v / 1_000)} тыс`;
  return String(Math.round(v));
}


export interface ChartSpec {
  /** Полилиния истории: [{x,y}] в координатах 0..width / 0..height. */
  line: { x: number; y: number }[];
  /** Текущая цена (точка на линии). */
  current: { x: number; y: number } | null;
  /** Диапазон рынка comps: y-координаты. */
  band: { top: number; bottom: number } | null;
  /** Подписи min/max шкалы. */
  scale: { min: number; max: number };
}


const W = 320;
const H = 120;
const PAD = 8;

/** История цен + диапазон comps → координаты SVG (0..W, 0..H). */
export function buildChartData(
  history: { price: number; recorded_at: string }[],
  comps: { min: number; max: number; count?: number; median?: number } | null,
): ChartSpec | null {
  const pts = history.filter((h) => h.price > 0);
  if (!pts.length) return null;
  const values = pts.map((p) => p.price);
  let min = Math.min(...values);
  let max = Math.max(...values);
  if (comps && comps.max > comps.min) {
    min = Math.min(min, comps.min);
    max = Math.max(max, comps.max);
  }
  if (max === min) max = min * 1.05 + 1; // одна точка — чуть растянем шкалу

  const y = (v: number) => PAD + (1 - (v - min) / (max - min)) * (H - 2 * PAD);
  const inner = W - 2 * PAD;
  const line = pts.map((p, i) => ({
    x: PAD + (pts.length === 1 ? inner / 2 : (i / (pts.length - 1)) * inner),
    y: y(p.price),
  }));
  return {
    line,
    current: line[line.length - 1],
    band: comps && comps.max > comps.min ? { top: y(comps.max), bottom: y(comps.min) } : null,
    scale: { min: Math.round(min), max: Math.round(max) },
  };
}

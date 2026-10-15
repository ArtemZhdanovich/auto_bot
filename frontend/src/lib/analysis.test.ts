/** Юнит-тесты математики AI-анализа (vitest, node — без React). */
import { describe, expect, it } from "vitest";

import { buildChartData, deltaPct, VERDICT_LABEL } from "./analysis";

describe("deltaPct", () => {
  it("цена ниже середины — отрицательный процент", () => {
    expect(deltaPct({ price: 8100, fair_min: 8000, fair_max: 10000 })).toBe(-10);
  });

  it("цена выше — положительный", () => {
    expect(deltaPct({ price: 11000, fair_min: 8000, fair_max: 10000 })).toBe(22);
  });

  it("в середине — 0", () => {
    expect(deltaPct({ price: 9000, fair_min: 8000, fair_max: 10000 })).toBe(0);
  });

  it("нет диапазона — null", () => {
    expect(deltaPct({ price: 9000, fair_min: null, fair_max: null })).toBeNull();
  });
});

describe("buildChartData", () => {
  const hist = [
    { price: 100, recorded_at: "2026-01-01" },
    { price: 110, recorded_at: "2026-02-01" },
    { price: 120, recorded_at: "2026-03-01" },
  ];

  it("линия из всех точек, current — последняя", () => {
    const spec = buildChartData(hist, null)!;
    expect(spec.line).toHaveLength(3);
    expect(spec.current).toEqual(spec.line[2]);
    // цена растёт → y убывает (в SVG вверх = меньше)
    expect(spec.line[2].y).toBeLessThan(spec.line[0].y);
  });

  it("band учитывает comps шире истории", () => {
    const spec = buildChartData(hist, { count: 5, min: 50, max: 200, median: 110 })!;
    expect(spec.band).not.toBeNull();
    // max шкалы теперь 200 (comps), min 50
    expect(spec.scale).toEqual({ min: 50, max: 200 });
  });

  it("одна точка — не падает, растянутая шкала", () => {
    const spec = buildChartData([hist[0]], null)!;
    expect(spec.line).toHaveLength(1);
    expect(spec.scale.max).toBeGreaterThan(spec.scale.min);
  });

  it("пустая история → null", () => {
    expect(buildChartData([], null)).toBeNull();
  });
});

describe("VERDICT_LABEL", () => {
  it("все три вердикта подписаны", () => {
    expect(Object.keys(VERDICT_LABEL).sort()).toEqual(["deal", "fair", "overpriced"]);
    expect(VERDICT_LABEL.deal.emoji).toBe("🟢");
  });
});

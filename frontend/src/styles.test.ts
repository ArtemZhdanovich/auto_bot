/**
 * Контракт вёрстки сайта (vitest, node — CSS проверяем как текст).
 * Гарантирует: закругления и тени на карточках, читаемый вторичный
 * текст (без слияния с фоном), тёмная тема для плашек-вердиктов.
 */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

const css = readFileSync(join(__dirname, "..", "src", "index.css"), "utf-8");

/** Тело CSS-правила по селектору (первое вхождение). */
function rule(selector: string): string {
  const idx = css.indexOf(selector);
  expect(idx, `селектор ${selector} должен существовать`).toBeGreaterThanOrEqual(0);
  const open = css.indexOf("{", idx);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

describe("дизайн-токены", () => {
  it("радиусы и тени определены как переменные", () => {
    expect(css).toContain("--radius-lg: 16px");
    expect(css).toContain("--shadow-md:");
    expect(css).toContain("--shadow-lg:");
  });
});

describe("карточки: закругления + тени", () => {
  const cards = [
    ".listing-card",
    ".calc-form",
    ".calc-rows",
    ".calc-legal",
    ".compare-variant",
    ".compare-item",
    ".analysis",
  ];
  it.each(cards)("%s имеет border-radius и box-shadow", (sel) => {
    const body = rule(sel);
    expect(body, `${sel}: border-radius`).toContain("border-radius: var(--radius");
    expect(body, `${sel}: box-shadow`).toContain("box-shadow: var(--shadow");
  });

  it("итоговая плашка калькулятора отбрасывает тень", () => {
    expect(rule(".calc-total")).toContain("box-shadow: var(--shadow-md)");
  });
});

describe("контраст: текст не сливается с фоном", () => {
  it("вторичные подписи — через var(--text-secondary), а не низкий opacity", () => {
    for (const sel of [
      ".calc-note",
      ".analysis__market",
      ".analysis__meta",
      ".analysis__hint",
      ".price-chart__scale",
      ".price-chart__legend",
    ]) {
      expect(rule(sel), `${sel}: цвет вторичного текста`).toContain(
        "color: var(--text-secondary)",
      );
    }
  });

  it("на компонентах с текстом нет жёсткого белого фона", () => {
    expect(css).not.toContain("background: rgba(255, 255, 255, 0.9)");
    expect(css).not.toMatch(/\.calc-[^{]*\{[^}]*background:\s*#fff/i);
  });

  it("карточка объявления отделена рамкой от фона ленты", () => {
    expect(rule(".listing-card")).toContain("border: 1px solid var(--bg-secondary)");
  });
});

describe("тёмная тема", () => {
  it("вердикты AI-анализа и бейджи перекрашены для dark", () => {
    const dark = css.slice(css.indexOf("prefers-color-scheme: dark"));
    expect(dark).toContain(".analysis__verdict--deal");
    expect(dark).toContain(".analysis__verdict--overpriced");
    expect(dark).toContain(".analysis__pill--good");
  });
});

describe("правовые основания расчёта в UI", () => {
  it("блок calc-legal оформлен как карточка", () => {
    const body = rule(".calc-legal");
    expect(body).toContain("border-radius: var(--radius-lg)");
    expect(body).toContain("box-shadow: var(--shadow-sm)");
    expect(rule(".calc-legal__ref")).toContain("color: var(--text-secondary)");
  });
});

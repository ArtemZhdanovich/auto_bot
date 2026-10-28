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
    expect(rule(".listing-card")).toContain("border: 1px solid var(--border)");
  });
});

describe("тёмная тема (data-theme на <html>, lib/theme.ts)", () => {
  it("палитра :root[data-theme=dark] перекрывает фон/текст/бордеры/тени", () => {
    const dark = rule(':root[data-theme="dark"]');
    expect(dark).toContain("color-scheme: dark");
    // Значения — сцена «закрытого автосалона» (02-design-system.md §8.2)
    expect(dark).toContain("--bg: #0f1115");
    expect(dark).toContain("--bg-secondary: #171a20");
    expect(dark).toContain("--surface: #1d2129");
    expect(dark).toContain("--text: #f0f2f5");
    expect(dark).toContain("--text-secondary: #9aa4b2");
    expect(dark).toContain("--shadow-md:");
    expect(dark).toContain("--border:");
  });

  it("семантические токены (--deal/--warn/--danger) заданы в обеих темах", () => {
    const light = rule(":root");
    const dark = rule(':root[data-theme="dark"]');
    for (const block of [light, dark]) {
      expect(block).toContain("--deal:");
      expect(block).toContain("--warn:");
      expect(block).toContain("--danger:");
      expect(block).toContain("--accent-ring:");
    }
    // тёмная не наследует фолбэки светлой: акцент светлее, текст на нём тёмный
    expect(dark).toContain("var(--tg-theme-button-color, #5ca9f0)");
    expect(dark).toContain("var(--tg-theme-button-text-color, #0f1115)");
  });

  it("z-шкала: тосты выше модалок (§5.3)", () => {
    const light = rule(":root");
    expect(light).toContain("--z-toast: 1100");
    expect(light).toContain("--z-modal: 1050");
    expect(rule(".toast-host")).toContain("z-index: var(--z-toast)");
    expect(rule(".modal")).toContain("z-index: var(--z-modal)");
  });

  it("не перекрывает --tg-theme-* напрямую (Telegram — источник акцента)", () => {
    expect(rule(':root[data-theme="dark"]')).not.toMatch(/--tg-theme-[a-z-]+:\s*#/);
  });

  it("вердикты AI-анализа и бейджи перекрашены для dark", () => {
    const dark = css.slice(css.indexOf('[data-theme="dark"]'));
    expect(dark).toContain(".analysis__verdict--deal");
    expect(dark).toContain(".analysis__verdict--overpriced");
    expect(dark).toContain(".analysis__pill--good");
  });

  it("грейды осмотра читаемы в dark (не светлые пастели под светлым текстом)", () => {
    const dark = css.slice(css.indexOf('[data-theme="dark"]'));
    expect(dark).toContain(".inspection__grade--excellent");
    expect(dark).toContain(".inspection__grade--poor");
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

describe("предупреждение и disabled-состояния", () => {
  it("предупреждение о льготе утильсбора — заметная плашка со скруглением", () => {
    const body = rule(".calc-warn");
    expect(body).toContain("border-radius: var(--radius-lg)");
    expect(body).toContain("border: 1px solid rgba(240, 178, 12");
  });

  it("тёмная тема: предупреждение читаемо (перекрашено, не тёмно-жёлтое на тёмном)", () => {
    const dark = css.slice(css.indexOf('[data-theme="dark"]'));
    expect(dark).toContain(".calc-warn");
  });

  it("у чипов есть disabled-стейт (без инлайн-opacity в компонентах)", () => {
    expect(rule(".chip:disabled")).toContain("cursor: not-allowed");
  });
});

describe("mobile ux (Фиттс/Хик, <768px — блок «Mobile UX» в конце index.css)", () => {
  /** Мобильный блок дописан в конец index.css; срезаем от его заголовка. */
  const mobile = css.slice(css.indexOf("Mobile UX 2026-10"));
  const mobileRule = (sel: string): string => {
    const idx = mobile.indexOf(sel);
    expect(idx, `селектор ${sel} в мобильном блоке`).toBeGreaterThanOrEqual(0);
    const open = mobile.indexOf("{", idx);
    const close = mobile.indexOf("}", open);
    return mobile.slice(open + 1, close);
  };

  it("чипы и мелкие контролы ленты ≥44px на мобиле (закон Фиттса)", () => {
    expect(mobileRule(".chip")).toContain("min-height: var(--touch-min)");
    expect(mobileRule(".feed-toggle")).toContain("min-height: var(--touch-min)");
    expect(mobileRule(".feed-sort")).toContain("min-height: var(--touch-min)");
  });

  it("ряд чипсов ленты скроллится со snap (Хик: один ряд, не выбор из всех)", () => {
    expect(rule(".feed-head__chips")).toContain("scroll-snap-type: x proximity");
  });

  it("«Ещё параметры»: кнопка-раскрытие с тач-зоной 44 и сеткой блока", () => {
    expect(rule(".feed-panel__more")).toContain("min-height: var(--touch-min)");
    expect(rule(".feed-panel__extra")).toContain("display: grid");
  });

  it("зона пальца: «Наверх» выше FAB, тост — над таббаром с safe-area", () => {
    expect(mobileRule(".to-top")).toContain("env(safe-area-inset-bottom");
    expect(rule(".toast-host")).toContain("env(safe-area-inset-bottom");
  });

  it("хит-зоны мелких иконок расширены псевдоэлементом, без раздувания", () => {
    for (const sel of [
      ".gallery__expand::after",
      ".pdp-dots__dot::after",
      ".inspection__pin::after",
    ]) {
      expect(rule(sel)).toContain("inset:");
    }
  });
});

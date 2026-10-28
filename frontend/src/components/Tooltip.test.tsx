/** Тесты Tooltip (02-design-system §7.13): портал, aria, закрытие, ширина. */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { afterEach, describe, expect, it } from "vitest";

import { Tooltip } from "./Tooltip";

const css = readFileSync(join(__dirname, "..", "index.css"), "utf-8");

/** Тело CSS-правила по селектору (как в styles.test.ts). Ищем селектор
 *  вместе с открывающей скобкой — иначе «.tooltip» найдёт «.tooltip-trigger». */
function rule(selector: string): string {
  const idx = css.indexOf(`${selector} {`);
  expect(idx, `селектор ${selector} должен существовать`).toBeGreaterThanOrEqual(0);
  const open = css.indexOf("{", idx);
  const close = css.indexOf("}", open);
  return css.slice(open + 1, close);
}

afterEach(() => cleanup());

/** Открыть тултип hover'ом (delay=0) и вернуть пузырь из портала. */
async function openByHover(ui: React.ReactElement) {
  const { container } = render(ui);
  const trigger = container.querySelector(".tooltip-trigger") as HTMLElement;
  fireEvent.mouseEnter(trigger);
  await waitFor(() => expect(document.querySelector(".tooltip")).not.toBeNull());
  return { trigger, bubble: document.querySelector(".tooltip") as HTMLElement };
}

describe("Tooltip: показ и aria", () => {
  it("hover показывает пузырь; aria-describedby на триггере указывает на него", async () => {
    const { trigger, bubble } = await openByHover(
      <Tooltip text="Курс конвертации: 1$ = 95₽">
        <button>Цена</button>
      </Tooltip>,
    );
    // пузырь отрендерен в портале (document.body), не внутри контейнера
    expect(bubble.closest(".tooltip-trigger")).toBeNull();
    expect(document.body.contains(bubble)).toBe(true);
    expect(bubble.getAttribute("role")).toBe("tooltip");
    // aria-describedby прокинут на сам триггер (кнопку), id совпадает
    const btn = trigger.querySelector("button") as HTMLElement;
    const describedBy = btn.getAttribute("aria-describedby");
    expect(describedBy).toBe(bubble.id);
    expect(bubble.textContent).toContain("Курс конвертации");
  });

  it("текстовый триггер: aria-describedby и focus на span-обёртке", async () => {
    const { trigger, bubble } = await openByHover(<Tooltip text="Подсказка">VIN</Tooltip>);
    expect(trigger.getAttribute("aria-describedby")).toBe(bubble.id);
    expect(trigger.getAttribute("tabindex")).toBe("0");
  });

  it("до наведения пузыря нет", () => {
    render(
      <Tooltip text="Спрятан" delay={60000}>
        <button>Кнопка</button>
      </Tooltip>,
    );
    expect(document.querySelector(".tooltip")).toBeNull();
  });
});

describe("Tooltip: закрытие", () => {
  it("Esc закрывает", async () => {
    const { trigger } = await openByHover(
      <Tooltip text="Текст">
        <button>Кнопка</button>
      </Tooltip>,
    );
    fireEvent.keyDown(trigger, { key: "Escape" });
    await waitFor(() => expect(document.querySelector(".tooltip")).toBeNull());
  });

  it("touch: тап показывает, повторный тап — закрывает", async () => {
    const { container } = render(
      <Tooltip text="Тач-подсказка">
        <button>Кнопка</button>
      </Tooltip>,
    );
    const trigger = container.querySelector(".tooltip-trigger") as HTMLElement;
    fireEvent.pointerDown(trigger, { pointerType: "touch" });
    await waitFor(() => expect(document.querySelector(".tooltip")).not.toBeNull());
    fireEvent.pointerDown(trigger, { pointerType: "touch" });
    await waitFor(() => expect(document.querySelector(".tooltip")).toBeNull());
  });
});

describe("Tooltip: вёрстка не ломается (контракт index.css)", () => {
  it("пузырь: max-width 240px, перенос слов, fixed-позиционирование", () => {
    const body = rule(".tooltip");
    expect(body).toContain("max-width: 240px");
    expect(body).toContain("word-wrap: break-word");
    expect(body).toContain("position: fixed");
    expect(body).toContain("z-index: var(--z-tooltip)");
    // появление 120мс (--dur-fast, дока §6.1)
    expect(css).toMatch(/animation:\s*tooltip-in\s+var\(--dur-fast\)/);
  });

  it("тёмная тема перекрашивает пузырь (surface + бордер)", () => {
    expect(rule(':root[data-theme="dark"] .tooltip')).toContain("background: var(--surface)");
  });
});

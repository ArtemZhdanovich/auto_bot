/** Юнит-тесты темы (vitest, jsdom: localStorage + dataset, по образцу fx.test.ts). */
import { beforeEach, describe, expect, it } from "vitest";

import { applyTheme, getTheme, setTheme } from "./theme";

/** jsdom не умеет matchMedia — стаб с настраиваемым prefers-color-scheme. */
function stubMatchMedia(dark: boolean): void {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (q: string) => ({
      matches: q.includes("dark") ? dark : false,
      media: q,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }),
  });
}

/** Тема Telegram (для system-режима tgAppearance смотрит на colorScheme). */
function stubTelegram(colorScheme: string | undefined): void {
  (window as { Telegram?: unknown }).Telegram = colorScheme
    ? { WebApp: { colorScheme } }
    : undefined;
}

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
  stubMatchMedia(false);
  stubTelegram(undefined);
});

describe("getTheme", () => {
  it("по умолчанию system", () => {
    expect(getTheme()).toBe("system");
  });

  it("читает сохранённый выбор из localStorage (autobot.theme)", () => {
    localStorage.setItem("autobot.theme", "dark");
    expect(getTheme()).toBe("dark");
    localStorage.setItem("autobot.theme", "light");
    expect(getTheme()).toBe("light");
  });

  it("мусор в localStorage → system", () => {
    localStorage.setItem("autobot.theme", "solarized");
    expect(getTheme()).toBe("system");
  });
});

describe("setTheme", () => {
  it("сохраняет выбор и сразу применяет (data-theme на <html>)", () => {
    const appearance = setTheme("dark");
    expect(localStorage.getItem("autobot.theme")).toBe("dark");
    expect(getTheme()).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(appearance).toBe("dark");
  });

  it("светлая — тоже применяется сразу", () => {
    setTheme("dark"); // предыдущее состояние
    expect(setTheme("light")).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });
});

describe("applyTheme", () => {
  it("явная тёмная: dataset.theme=dark, возвращает dark", () => {
    expect(applyTheme("dark")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("явная светлая: dataset.theme=light", () => {
    expect(applyTheme("light")).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("system без Telegram берёт prefers-color-scheme ОС", () => {
    stubMatchMedia(true);
    expect(applyTheme("system")).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    stubMatchMedia(false);
    expect(applyTheme("system")).toBe("light");
  });

  it("system в Telegram берёт colorScheme миниаппа", () => {
    stubMatchMedia(false); // ОС светлая, но Telegram тёмный
    stubTelegram("dark");
    expect(applyTheme("system")).toBe("dark");
    stubTelegram("light");
    expect(applyTheme("system")).toBe("light");
  });

  it("явный выбор перекрывает и Telegram, и ОС", () => {
    stubMatchMedia(true);
    stubTelegram("dark");
    expect(applyTheme("light")).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("без аргумента применяет сохранённый выбор", () => {
    localStorage.setItem("autobot.theme", "dark");
    expect(applyTheme()).toBe("dark");
  });
});

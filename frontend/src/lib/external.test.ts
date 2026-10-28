/** Тесты хелперов открытия/шаринга внешних ссылок (jsdom). */
import { afterEach, describe, expect, it, vi } from "vitest";

import { getWebApp } from "../api/auth";
import { openExternal, shareListingCard, type ShareListingCardInput } from "./external";

vi.mock("../api/auth", () => ({
  getWebApp: vi.fn(() => null),
}));

/** Карточка для тестов: текст «Kia K5, 2021 — 21 900 000 KRW» (цена — как
 *  в коде, через toLocaleString("ru"), чтобы не зависеть от разделителей ICU). */
const card: ShareListingCardInput = {
  id: 77,
  make: "Kia",
  model: "K5",
  year: 2021,
  price: 21_900_000,
  currency: "KRW",
};
const cardTitle = `Kia K5, 2021 — ${(21_900_000).toLocaleString("ru")} KRW`;

/** Подменить свойство navigator (share/clipboard) на время теста. */
function stubNavigator(prop: "share" | "clipboard", value: unknown) {
  Object.defineProperty(navigator, prop, {
    value,
    configurable: true,
    writable: true,
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  delete (navigator as Record<string, unknown>).share;
  delete (navigator as Record<string, unknown>).clipboard;
});

describe("shareListingCard", () => {
  it("в Telegram открывает нативный диалог t.me/share/url с карточкой и ссылкой вебаппа", async () => {
    const openTelegramLink = vi.fn();
    vi.mocked(getWebApp).mockReturnValue({
      initData: "x",
      ready: vi.fn(),
      expand: vi.fn(),
      openTelegramLink,
    });
    expect(await shareListingCard(card)).toBe(true);
    const url = `${location.origin}/listing/77`;
    expect(openTelegramLink).toHaveBeenCalledWith(
      `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(cardTitle)}`,
    );
  });

  it("в Telegram использует deep-link бота, если известен его username", async () => {
    const openTelegramLink = vi.fn();
    vi.mocked(getWebApp).mockReturnValue({
      initData: "x",
      ready: vi.fn(),
      expand: vi.fn(),
      openTelegramLink,
      initDataUnsafe: { bot: { username: "autobot_test_bot" } },
    } as unknown as NonNullable<ReturnType<typeof getWebApp>>);
    expect(await shareListingCard(card)).toBe(true);
    const url = "https://t.me/autobot_test_bot?startapp=listing_77";
    expect(openTelegramLink).toHaveBeenCalledWith(
      `https://t.me/share/url?url=${encodeURIComponent(url)}&text=${encodeURIComponent(cardTitle)}`,
    );
  });

  it("в браузере с Web Share API вызывает navigator.share и возвращает true", async () => {
    const share = vi.fn().mockResolvedValue(undefined);
    stubNavigator("share", share);
    vi.mocked(getWebApp).mockReturnValue(null);
    expect(await shareListingCard(card)).toBe(true);
    const url = `${location.origin}/listing/77`;
    expect(share).toHaveBeenCalledWith({ title: cardTitle, text: cardTitle, url });
  });

  it("в браузере без share копирует карточку в буфер и возвращает false (тост «скопировано»)", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubNavigator("clipboard", { writeText });
    vi.mocked(getWebApp).mockReturnValue(null);
    expect(await shareListingCard(card)).toBe(false);
    const url = `${location.origin}/listing/77`;
    expect(writeText).toHaveBeenCalledWith(`${cardTitle}\n${url}`);
  });

  it("отмена navigator.share (AbortError) — без копирования и без тоста (true)", async () => {
    const share = vi.fn().mockRejectedValue(
      Object.assign(new Error("cancel"), { name: "AbortError" }),
    );
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubNavigator("share", share);
    stubNavigator("clipboard", { writeText });
    vi.mocked(getWebApp).mockReturnValue(null);
    expect(await shareListingCard(card)).toBe(true);
    expect(writeText).not.toHaveBeenCalled();
  });
});

describe("openExternal", () => {
  it("в Telegram открывает через WebApp.openLink", () => {
    const openLink = vi.fn();
    vi.mocked(getWebApp).mockReturnValue({
      initData: "x",
      ready: vi.fn(),
      expand: vi.fn(),
      openLink,
    });
    openExternal("https://example.com/1");
    expect(openLink).toHaveBeenCalledWith(
      "https://example.com/1",
      { try_instant_view: false },
    );
  });
});

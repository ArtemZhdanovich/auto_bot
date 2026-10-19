/** Тесты хелперов открытия/шаринга внешних ссылок (jsdom). */
import { afterEach, describe, expect, it, vi } from "vitest";

import { getWebApp } from "../api/auth";
import { openExternal, shareListing } from "./external";

vi.mock("../api/auth", () => ({
  getWebApp: vi.fn(() => null),
}));

afterEach(() => {
  vi.restoreAllMocks();
});

describe("shareListing", () => {
  it("в Telegram вызывает switchInlineQuery с запросом listing:<id>", async () => {
    const switchInlineQuery = vi.fn();
    vi.mocked(getWebApp).mockReturnValue({
      initData: "x",
      ready: vi.fn(),
      expand: vi.fn(),
      switchInlineQuery,
    });
    expect(shareListing(77)).toBe(true);
    expect(switchInlineQuery).toHaveBeenCalledWith("listing:77");
  });

  it("вне Telegram возвращает false (вызывающий показывает тост)", () => {
    vi.mocked(getWebApp).mockReturnValue(null);
    expect(shareListing(77)).toBe(false);
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

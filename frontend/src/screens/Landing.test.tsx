/** Тесты лендинга-входа (NAV-V2 §1): показывается в браузере на «/»
 *  ВСЕГДА — последний выбор лишь ставит карточку первой с меткой
 *  «● последний вход», ничего не запирает. */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  authorize: vi.fn(),
  guestLogin: vi.fn(),
  isGuest: vi.fn((): boolean => true),
  clearGuest: vi.fn(),
  api: vi.fn(),
  getAccessToken: vi.fn((): string | null => null),
  isTelegramContext: vi.fn((): boolean => false),
}));

vi.mock("../api/auth", () => ({
  authorize: mocks.authorize,
  guestLogin: mocks.guestLogin,
  isGuest: mocks.isGuest,
  clearGuest: mocks.clearGuest,
  getWebApp: () => null,
  isTelegramContext: mocks.isTelegramContext,
  haptic: vi.fn(),
  logout: vi.fn(),
  tgUser: () => null,
  webLogin: vi.fn(),
  webRegister: vi.fn(),
}));

vi.mock("../api/client", () => ({
  api: mocks.api,
  getAccessToken: mocks.getAccessToken,
  getRefreshToken: vi.fn(),
  setTokens: vi.fn(),
  clearTokens: vi.fn(),
  setSessionExpiredHandler: vi.fn(),
  ApiError: class extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

import { App } from "../App";

const USER = {
  telegram_id: -1,
  username: null,
  first_name: "Гость",
  last_name: null,
  language_code: null,
  role: "buyer",
  subscription_tier: "free",
  subscription_until: null,
  onboarded_at: "2026-01-01",
  created_at: "2026-01-01",
  is_active: true,
};

describe("Landing: вход-выбор приложения (NAV-V2: всегда на «/»)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocks.isTelegramContext.mockReturnValue(false);
    mocks.authorize.mockResolvedValue(null);
    mocks.guestLogin.mockResolvedValue({ access_token: "a", refresh_token: "r", user: USER });
    mocks.api.mockResolvedValue([]);
    window.history.replaceState(null, "", "/");
    vi.stubGlobal("IntersectionObserver", class {
      observe() {} unobserve() {} disconnect() {}
    });
    Element.prototype.scrollIntoView = () => undefined;
    Object.defineProperty(window, "matchMedia", {
      writable: true,
      value: (q: string) => ({
        matches: false,
        media: q,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
      }),
    });
  });

  afterEach(cleanup);

  it("в браузере на «/» — лендинг с двумя входами", () => {
    render(<App />);
    expect(screen.getByTestId("landing")).toBeTruthy();
    expect(
      screen.getByText("Приложение для пользователей"),
    ).toBeTruthy();
    expect(screen.getByText("Кабинет партнёра")).toBeTruthy();
    // приложение ещё не запускалось — гостя не заводим
    expect(mocks.guestLogin).not.toHaveBeenCalled();
  });

  it("кнопка партнёра ведёт на /partner", () => {
    render(<App />);
    expect(screen.getByTestId("landing-partner").getAttribute("href")).toBe("/partner");
  });

  it("выбор «пользователям» запускает Mini App без перезагрузки", async () => {
    render(<App />);
    fireEvent.click(screen.getByText("Приложение для пользователей"));
    // выбор зафиксирован и приложение смонтировалось (гостевой вход пошёл)
    expect(localStorage.getItem("autobot.surface")).toBe("user");
    await (() => new Promise((r) => setTimeout(r, 0)))();
    expect(mocks.guestLogin).toHaveBeenCalled();
  });

  it("в Telegram лендинга нет — сразу приложение", async () => {
    mocks.isTelegramContext.mockReturnValue(true);
    render(<App />);
    expect(screen.queryByTestId("landing")).toBeNull();
  });

  it("deep-link с query-критериями на «/» — сразу приложение, без лендинга", () => {
    window.history.replaceState(null, "", "/?make=Kia&price_max=25000");
    render(<App />);
    expect(screen.queryByTestId("landing")).toBeNull();
  });

  it("повторный визит (surface=user): лендинг всё равно показан, карточка пользователей первой с «● последний вход»", () => {
    localStorage.setItem("autobot.surface", "user");
    render(<App />);
    expect(screen.getByTestId("landing")).toBeTruthy();
    expect(screen.getByText("● последний вход").closest("button")?.textContent)
      .toContain("Приложение для пользователей");
    // метка на карточке партнёра не дублируется
    const partner = screen.getByTestId("landing-partner");
    expect(partner.textContent).not.toContain("последний вход");
  });

  it("surface=partner: карточка партнёра первой с меткой, вход в Mini App ниже", () => {
    localStorage.setItem("autobot.surface", "partner");
    render(<App />);
    const cards = Array.from(document.querySelectorAll(".landing__card"));
    expect(cards[0]).toBe(screen.getByTestId("landing-partner"));
    expect(cards[0].textContent).toContain("● последний вход");
    expect(cards[1]?.textContent).toContain("Приложение для пользователей");
    expect(cards[1]?.textContent).not.toContain("последний вход");
  });
});

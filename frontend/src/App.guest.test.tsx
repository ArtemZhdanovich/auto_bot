/** Компонентные тесты гостевого режима сайта (jsdom + testing-library).
 *
 * Сценарий «незалогиненный посетитель открыл сайт в браузере»:
 * initData нет → автоматический гостевой вход → лента и демо-баннер;
 * /login доступен гостю; Personal-функции ведут к полноценному входу.
 */
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// --- Моки API-слоя (App и экраны импортируют только эти два модуля) ---

const mocks = vi.hoisted(() => ({
  authorize: vi.fn(),
  guestLogin: vi.fn(),
  isGuest: vi.fn((): boolean => true),
  clearGuest: vi.fn(),
  api: vi.fn(),
  getAccessToken: vi.fn((): string | null => null),
}));

vi.mock("./api/auth", () => ({
  authorize: mocks.authorize,
  guestLogin: mocks.guestLogin,
  isGuest: mocks.isGuest,
  clearGuest: mocks.clearGuest,
  getWebApp: () => null,
  isTelegramContext: () => false,
  haptic: vi.fn(),
  logout: vi.fn(),
  tgUser: () => null,
  webLogin: vi.fn(),
  webRegister: vi.fn(),
}));

vi.mock("./api/client", () => ({
  api: mocks.api,
  getAccessToken: mocks.getAccessToken,
  setTokens: vi.fn(),
  clearTokens: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(status: number, message: string) {
      super(message);
      this.status = status;
    }
  },
}));

import { App } from "./App";

const LISTING = {
  id: 7,
  url: "https://encar.com/x",
  source: "encar",
  make: "Kia",
  model: "K5",
  year: 2022,
  price: 18_400_000,
  currency: "KRW",
  mileage_km: 32_000,
  fuel: "gasoline",
  transmission: "auto",
  region: null,
  parsed_at: "2026-01-01T00:00:00Z",
  photo_url: null,
  title: null,
  images: ["https://img.example/k5.jpg"],
  first_price: null,
};
const USER = {
  telegram_id: -123,
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

afterEach(cleanup);

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getAccessToken.mockReturnValue(null);
  mocks.isGuest.mockReturnValue(true);
  mocks.authorize.mockResolvedValue(null); // вне Telegram
  mocks.guestLogin.mockResolvedValue({ access_token: "a", refresh_token: "r", user: USER });
  mocks.api.mockImplementation(async (path: string) => {
    if (path.startsWith("/listings")) return { total: 1, items: [LISTING] };
    if (path === "/filters") return [];
    if (path === "/me") return USER;
    if (path === "/me/onboarded") return undefined;
    return undefined;
  });
  // jsdom не умеет IntersectionObserver (Feed: бесконечный скролл)
  vi.stubGlobal("IntersectionObserver", class {
    observe() {} unobserve() {} disconnect() {}
  });
  // jsdom не умеет matchMedia (useIsDesktop)
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: (q: string) => ({
      matches: false,
      media: q,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }),
  });
  window.history.replaceState(null, "", "/");
});

describe("гостевой режим сайта", () => {
  it("вне Telegram происходит гостевой вход, видна лента и демо-баннер", async () => {
    render(<App />);
    await waitFor(() => expect(mocks.guestLogin).toHaveBeenCalledOnce());
    // лента загрузилась
    await waitFor(() => expect(screen.getByText(/Kia/)).toBeTruthy());
    // демо-баннер гостя
    expect(screen.getByText(/Демо без регистрации/)).toBeTruthy();
    // ссылка «Войти» ведёт на /login
    expect(screen.getByText("Войти").getAttribute("href")).toBe("/login");
  });

  it("баннер скрывается крестиком (в рамках сессии)", async () => {
    render(<App />);
    await waitFor(() => screen.getByText(/Демо без регистрации/));
    screen.getByLabelText("Скрыть").click();
    await waitFor(() => expect(screen.queryByText(/Демо без регистрации/)).toBeNull());
  });

  it("гостевой /login показывает форму входа (апгрейд из гостя)", async () => {
    window.history.replaceState(null, "", "/login");
    render(<App />);
    // «Вход» есть и в заголовке формы, и в табе-переключателе — проверяем наличие
    await waitFor(() => expect(screen.getAllByText("Вход").length).toBeGreaterThan(0));
    expect(mocks.guestLogin).toHaveBeenCalled(); // гость всё равно был заведён
  });

  it("у авторизованного Telegram-юзера гостевого входа нет и баннера нет", async () => {
    mocks.authorize.mockResolvedValue({ access_token: "a", refresh_token: "r", user: USER });
    mocks.isGuest.mockReturnValue(false);
    render(<App />);
    await waitFor(() => screen.getByText(/Kia/));
    expect(mocks.guestLogin).not.toHaveBeenCalled();
    expect(screen.queryByText(/Демо без регистрации/)).toBeNull();
  });
});

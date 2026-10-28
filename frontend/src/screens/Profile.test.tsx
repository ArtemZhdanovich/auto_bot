/**
 * Тесты Профиля (01-ux-audit-ia.md §1.8, R1–R3; тексты — 03 §2.9):
 *  R1 — подписка/тарифы выше настроек оформления;
 *  R2 — кнопки покупки по иерархии (free → Pro/Business);
 *  R3 — «Выйти» — ghost-кнопка внизу с подтверждением;
 *  NAV-V2 §1 — «Сменить поверхность»: сброс выбора + выход на лендинг «/».
 *
 * «Мои объявления» (NAV-V2) переехали в таб «Авто» — tests in
 * screens/MyLotsPanel.test.tsx.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfileScreen } from "./Profile";
import { api } from "../api/client";
import { logout } from "../api/auth";

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../api/auth", () => ({
  haptic: vi.fn(),
  isGuest: vi.fn(() => false),
  logout: vi.fn(),
  tgUser: vi.fn(() => null),
}));
vi.mock("../lib/invoice", () => ({ openInvoice: vi.fn() }));

const apiMock = vi.mocked(api);
const logoutMock = vi.mocked(logout);

const USER = {
  telegram_id: 1,
  username: "buyer",
  first_name: "Иван",
  last_name: null,
  language_code: "ru",
  role: "buyer",
  subscription_tier: "free",
  subscription_until: null,
  created_at: "2026-01-01T00:00:00Z",
  is_active: true,
  onboarded_at: "2026-01-01T00:00:00Z",
};

const SUB_FREE = {
  tier: "free",
  paid_tier: "free",
  subscription_until: null,
  max_filters: 3,
  ai_daily_limit: 1,
  instant: false,
  prices: { pro_month: 500, pro_year: 4800, business_month: 2500 },
  digest: "weekly",
  inspection: false,
  agent: false,
  analyses_limit: 1,
  analyses_window_days: 7,
};

const renderScreen = (sub: object | null = SUB_FREE) => {
  apiMock.mockImplementation(async (path: string) => {
    if (path === "/me") return USER;
    if (path === "/subscription") return sub;
    if (path === "/filters") return [];
    return undefined;
  });
  return render(
    <MemoryRouter initialEntries={["/profile"]} initialIndex={0}>
      <ProfileScreen />
    </MemoryRouter>,
  );
};

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe("Профиль: порядок панелей и тарифы (R1/R2)", () => {
  it("«Подписка» выше «Профиль» и «Темы оформления»; free видит кнопки Pro/Business", async () => {
    renderScreen();
    const sub = await screen.findByText("Подписка");
    const tierGrid = await screen.findByText("Тарифы");
    const profile = screen.getByText("С нами с").closest("section")!;
    const theme = screen.getByText("Тема оформления").closest("section")!;
    // R1: монетизация раньше настроек и карточки пользователя
    expect(sub.compareDocumentPosition(theme) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(sub.compareDocumentPosition(profile) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(tierGrid.compareDocumentPosition(theme) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // R2: иерархия покупки — Pro/мес (primary), Pro/год, Business
    expect(screen.getByRole("button", { name: "⭐ Pro — 500★/мес" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "⭐ Pro — 4800★/год (−20%)" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "⭐ Business — 2500★/мес" })).toBeTruthy();
    // сравнение тарифов с бейджем «текущий»
    expect(screen.getByText("текущий")).toBeTruthy();
  });

  it("business без даты — «бессрочная (партнёрская)», покупок нет", async () => {
    renderScreen({ ...SUB_FREE, tier: "business", subscription_until: null, max_filters: null, instant: true, digest: "instant" });
    expect(await screen.findByText("бессрочная (партнёрская)")).toBeTruthy();
    expect(screen.getByText("Это максимальный тариф — все возможности уже открыты")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Pro —/ })).toBeNull();
  });
});

describe("Профиль: выход с подтверждением (R3)", () => {
  it("«Выйти» — ghost внизу; случайный тап не разлогинивает, подтверждение — зовёт logout", async () => {
    renderScreen();
    const btn = await screen.findByRole("button", { name: "Выйти (сбросить сессию)" });
    expect(btn.className).toContain("pr-logout"); // ghost, не danger-плита
    // первый тап — только подтверждение
    fireEvent.click(btn);
    expect(logoutMock).not.toHaveBeenCalled();
    expect(screen.getByText("Точно выйти? Сессия сбросится.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Остаться" }));
    expect(logoutMock).not.toHaveBeenCalled();
    // подтверждённый выход
    fireEvent.click(screen.getByRole("button", { name: "Выйти (сбросить сессию)" }));
    fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(logoutMock).toHaveBeenCalled();
  });
});

describe("Профиль: «Сменить поверхность» (NAV-V2 §1)", () => {
  it("сбрасывает последний выбор и ведёт на «/» (лендинг-вход)", async () => {
    localStorage.setItem("autobot.surface", "user");
    // jsdom: подменяем location плейн-объектом (навигация не реализована)
    const orig = window.location;
    const assign = vi.fn();
    delete (window as { location?: Location }).location;
    (window as unknown as { location: unknown }).location = { assign };
    try {
      renderScreen();
      fireEvent.click(
        await screen.findByRole("button", { name: /Сменить поверхность/ }),
      );
      // выбор сброшен — лендинг на «/» откроется без «● последний вход»
      expect(localStorage.getItem("autobot.surface")).toBeNull();
      expect(assign).toHaveBeenCalledWith("/");
    } finally {
      delete (window as { location?: unknown }).location;
      (window as unknown as { location: unknown }).location = orig;
    }
  });
});

/**
 * Тесты «Сигналов» (01-ux-audit-ia.md §1.5, S1–S6; тексты — 03 §2.6):
 *  S1 — список карточками сверху, создание по кнопке «+ Новый сигнал»;
 *  S3 — счётчик N/M, апселл лимита Free-3;
 *  S4 — удаление только через «Точно удалить / Отмена»;
 *  S5 — редактирование тапом по карточке (PATCH);
 *  inline-валидация цена от>до — текст 03 §3.2.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FiltersScreen } from "./Filters";
import { api } from "../api/client";

const { toastShow } = vi.hoisted(() => ({ toastShow: vi.fn() }));

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../api/auth", () => ({ haptic: vi.fn() }));
vi.mock("../ui/Toast", () => ({ useToast: () => ({ show: toastShow }) }));

const apiMock = vi.mocked(api);

import type { Subscription, UserFilter } from "../api/types";

const SUB_FREE: Subscription = {
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

const filter = (id: number, over: Partial<UserFilter> = {}): UserFilter => ({
  id,
  name: null,
  criteria: {},
  notify_mode: "digest",
  quiet_hours: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: null,
  ...over,
});

const FILTERS: UserFilter[] = [
  filter(1, {
    name: "Семейный кроссовер",
    criteria: { make: "Kia", model: "Sorento", price_max: 45000 },
    notify_mode: "digest",
  }),
  filter(2, {
    name: "Carnival дизель",
    criteria: { make: "Kia", model: "Carnival", price_max: 50000 },
    notify_mode: "instant",
  }),
];

/** Мок API по пути+методу (GET /filters, GET /subscription, POST/PATCH/DELETE). */
function mockApi(filters: UserFilter[], sub: Subscription | null = SUB_FREE) {
  apiMock.mockImplementation(async (path: string, opts?: RequestInit) => {
    const method = opts?.method ?? "GET";
    if (path === "/subscription") return sub;
    if (path === "/filters" && method === "GET") return filters;
    if (path === "/filters" && method === "POST") return filter(99);
    if (/^\/filters\/\d+$/.test(path) && method === "DELETE") return undefined;
    if (/^\/filters\/\d+$/.test(path) && method === "PATCH")
      return filter(Number(path.split("/")[2]));
    return undefined;
  });
}

const renderScreen = () =>
  render(
    <MemoryRouter initialEntries={["/filters"]} initialIndex={0}>
      <FiltersScreen />
    </MemoryRouter>,
  );

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => cleanup());

describe("Сигналы: заголовок и список карточками (S1)", () => {
  it("экран называется «Сигналы», счётчик N/M, карточки с критериями и режимом", async () => {
    mockApi(FILTERS);
    renderScreen();
    expect(screen.getByText("Сигналы")).toBeTruthy();
    // критерии человекочитаемо (после загрузки списка и тарифа)
    expect(await screen.findByText("Kia, Sorento, до $ 45000")).toBeTruthy();
    expect(screen.getByText("2/3")).toBeTruthy();
    // режим доставки: digest по тарифу free = раз в неделю; instant = Business
    expect(screen.getByText("📋 дайджест: раз в неделю по тарифу")).toBeTruthy();
    expect(screen.getByText("⚡ сразу при публикации · Business")).toBeTruthy();
  });

  it("пусто: «Уведомлений пока нет» + CTA «Создать» открывает редактор", async () => {
    mockApi([]);
    renderScreen();
    expect(await screen.findByText("Уведомлений пока нет")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Создать" }));
    expect(screen.getByText("Новый сигнал")).toBeTruthy();
  });
});

describe("Сигналы: создание с валидацией (03 §3.2)", () => {
  it("валидация: цена от > до — человеческий текст, запрос не уходит", async () => {
    mockApi([]);
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Создать" }));
    fireEvent.change(screen.getByPlaceholderText("40000"), { target: { value: "15000" } });
    fireEvent.change(screen.getByPlaceholderText("15000"), { target: { value: "40000" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать уведомление" }));
    expect(
      await screen.findByText(/«Цена от» больше «цены до»/),
    ).toBeTruthy();
    expect(apiMock.mock.calls.filter(([p, o]) => p === "/filters" && o?.method === "POST")).toHaveLength(0);
  });

  it("успешное создание: POST с критериями и digest, тост «напишем в Telegram»", async () => {
    mockApi([]);
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Создать" }));
    fireEvent.change(screen.getByPlaceholderText("BMW"), { target: { value: "BMW" } });
    fireEvent.change(screen.getByPlaceholderText("40000"), { target: { value: "40000" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать уведомление" }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([p, o]) => p === "/filters" && o?.method === "POST")).toBe(true),
    );
    const call = apiMock.mock.calls.find(([p, o]) => p === "/filters" && o?.method === "POST")!;
    const body = JSON.parse((call[1] as RequestInit).body as string);
    expect(body.criteria).toEqual({ make: "BMW", price_max: 40000 });
    expect(body.notify_mode).toBe("digest");
    expect(toastShow).toHaveBeenCalledWith("Уведомление создано — напишем в Telegram");
  });

  it("403 лимит Free при создании — текст апселла вместо сырого detail", async () => {
    mockApi(FILTERS);
    apiMock.mockImplementation(async (path: string, opts?: RequestInit) => {
      const method = opts?.method ?? "GET";
      if (path === "/subscription") return SUB_FREE;
      if (path === "/filters" && method === "GET") return FILTERS;
      if (path === "/filters" && method === "POST")
        throw Object.assign(new Error("Filter limit reached (3 on free tier)"), { status: 403 });
      return undefined;
    });
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "+ Новый сигнал" }));
    fireEvent.change(screen.getByPlaceholderText("BMW"), { target: { value: "Audi" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать уведомление" }));
    expect(await screen.findByText(/Лимит уведомлений Free — 3/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Смотреть тарифы" })).toBeTruthy();
  });
});

describe("Сигналы: лимит Free-3 (S3)", () => {
  it("при 3/3 free — баннер апселла с кнопками «Смотреть тарифы / Удалить лишнее»", async () => {
    mockApi([filter(1), filter(2), filter(3)]);
    renderScreen();
    expect(await screen.findByText("Лимит уведомлений тарифа Free — 3")).toBeTruthy();
    expect(
      screen.getByText("Удалите ненужное или оформите Pro: до 10 уведомлений и ежедневный дайджест."),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Смотреть тарифы" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Удалить лишнее" })).toBeTruthy();
    expect(screen.getByText("3/3")).toBeTruthy();
  });
});

describe("Сигналы: удаление с подтверждением (S4) и редактирование (S5)", () => {
  it("✕ → «Точно удалить / Отмена»; Отмена не зовёт DELETE, подтверждение — зовёт", async () => {
    mockApi(FILTERS);
    renderScreen();
    const del = await screen.findByRole("button", { name: "Удалить Семейный кроссовер" });
    fireEvent.click(del);
    expect(screen.getByText(/Удалить уведомление «Семейный кроссовер»\?/)).toBeTruthy();
    // Отмена: ничего не удалено
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(apiMock.mock.calls.some(([p, o]) => o?.method === "DELETE")).toBe(false);
    expect(screen.getByText("Семейный кроссовер")).toBeTruthy();
    // Точно удалить → DELETE /filters/1 + тост
    fireEvent.click(screen.getByRole("button", { name: "Удалить Семейный кроссовер" }));
    fireEvent.click(screen.getByRole("button", { name: "Точно удалить" }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([p, o]) => p === "/filters/1" && o?.method === "DELETE")).toBe(true),
    );
    expect(toastShow).toHaveBeenCalledWith("Уведомление удалено");
  });

  it("тап по карточке открывает редактор с префиллом, «Сохранить сигнал» → PATCH", async () => {
    mockApi(FILTERS);
    renderScreen();
    fireEvent.click(await screen.findByRole("button", { name: "Редактировать сигнал Семейный кроссовер" }));
    expect(screen.getByText("Редактирование")).toBeTruthy();
    expect((screen.getByPlaceholderText("BMW") as HTMLInputElement).value).toBe("Kia");
    expect((screen.getByPlaceholderText("40000") as HTMLInputElement).value).toBe("45000");
    fireEvent.change(screen.getByPlaceholderText("40000"), { target: { value: "50000" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить сигнал" }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([p, o]) => p === "/filters/1" && o?.method === "PATCH")).toBe(true),
    );
    const call = apiMock.mock.calls.find(([p, o]) => p === "/filters/1" && o?.method === "PATCH")!;
    expect(JSON.parse((call[1] as RequestInit).body as string).criteria.price_max).toBe(50000);
  });
});

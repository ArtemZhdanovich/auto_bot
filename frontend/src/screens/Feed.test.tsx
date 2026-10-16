/** Тесты пагинации ленты: «Показать ещё», ретрай после ошибки, конец списка. */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FeedScreen } from "./Feed";
import { api } from "../api/client";
import type { Listing } from "../api/types";

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../api/auth", () => ({ haptic: vi.fn() }));
vi.mock("../ui/Toast", () => ({ useToast: () => ({ show: vi.fn() }) }));

// jsdom не знает IntersectionObserver — бесконечный скролл в тестах отключён,
// подгрузка проверяется кнопкой «Показать ещё».
vi.stubGlobal(
  "IntersectionObserver",
  class {
    observe() {}
    unobserve() {}
    disconnect() {}
  },
);

const apiMock = vi.mocked(api);

const listing = (id: number): Listing => ({
  id,
  source: "kcar",
  source_id: `X${id}`,
  url: `https://example.com/${id}`,
  make: "Kia",
  model: "Carnival",
  year: 2022,
  price: 18_400_000,
  currency: "KRW",
  mileage_km: 42_000,
  vin: null,
  images: [`https://img/${id}.jpg`],
  parsed_at: "2026-10-01T00:00:00Z",
  is_active: true,
  first_seen_at: null,
  first_price: null,
  price_history: null,
  title_raw: null,
  title_ru: null,
});

const page = (offset: number, total: number, size: number) => ({
  total,
  items: Array.from({ length: size }, (_, i) => listing(offset + i + 1)),
});

const TOTAL = 45;

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockImplementation(async (path: string) => {
    if (path.startsWith("/filters")) return [];
    const offset = Number(new URLSearchParams(path.split("?")[1]).get("offset") ?? 0);
    return page(offset, TOTAL, Math.min(20, TOTAL - offset));
  });
});

afterEach(() => cleanup());

const renderFeed = () =>
  render(
    <MemoryRouter>
      <FeedScreen />
    </MemoryRouter>,
  );

describe("Feed pagination", () => {
  it("загружает первую страницу и предлагает «Показать ещё» со счётчиком", async () => {
    renderFeed();
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Kia/ }).length).toBe(20));
    expect(screen.getByText("Показать ещё")).toBeTruthy();
    expect(screen.getByText("20 из 45")).toBeTruthy();
  });

  it("кнопка «Показать ещё» догружает страницы до конца списка", async () => {
    renderFeed();
    await waitFor(() => expect(screen.getByText("Показать ещё")).toBeTruthy());
    // страница 2: offset=20 → ещё 20 карточек
    fireEvent.click(screen.getByText("Показать ещё"));
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Kia/ }).length).toBe(40));
    expect(screen.getByText("40 из 45")).toBeTruthy();
    // страница 3: последние 5 → кнопка исчезает, плашка «все показаны»
    fireEvent.click(screen.getByText("Показать ещё"));
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Kia/ }).length).toBe(45));
    expect(screen.queryByText("Показать ещё")).toBeNull();
    expect(screen.getByText("Показаны все найденные варианты")).toBeTruthy();
  });

  it("при ошибке догрузки показывает ретрай и дозагружает после клика", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/filters")) return [];
      const offset = Number(new URLSearchParams(path.split("?")[1]).get("offset") ?? 0);
      if (offset > 0) throw new Error("502");
      return page(0, TOTAL, 20);
    });
    renderFeed();
    await waitFor(() => expect(screen.getByText("Показать ещё")).toBeTruthy());
    fireEvent.click(screen.getByText("Показать ещё"));
    await waitFor(() =>
      expect(screen.getByText("Не удалось загрузить следующие объявления")).toBeTruthy(),
    );
    // вторая попытка — успешная (mockImplementation из beforeEach уже нет,
    // восстанавливаем поведение вручную)
    apiMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/filters")) return [];
      const offset = Number(new URLSearchParams(path.split("?")[1]).get("offset") ?? 0);
      return page(offset, TOTAL, Math.min(20, TOTAL - offset));
    });
    fireEvent.click(screen.getByText("Повторить"));
    await waitFor(() => expect(screen.getAllByRole("button", { name: /Kia/ }).length).toBe(40));
    expect(screen.queryByText("Не удалось загрузить следующие объявления")).toBeNull();
    expect(screen.getByText("40 из 45")).toBeTruthy();
  });

  it("чип цены шлёт price_max в вонах ($10 000 × 1350)", async () => {
    renderFeed();
    await waitFor(() => expect(screen.getByText("до $10 000")).toBeTruthy());
    fireEvent.click(screen.getByText("до $10 000"));
    await waitFor(() => {
      const listingCalls = apiMock.mock.calls.map((c) => String(c[0])).filter((p) => p.includes("/listings"));
      expect(listingCalls.some((p) => p.includes("price_max=13500000"))).toBe(true);
    });
  });

  it("панель фильтров: цена и год конвертируются/пробрасываются, кнопка показывает счётчик", async () => {
    renderFeed();
    await waitFor(() => expect(screen.getByRole("button", { name: /Фильтры/ })).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Фильтры/ }));
    fireEvent.change(screen.getByLabelText("Цена $ до"), { target: { value: "5000" } });
    fireEvent.change(screen.getByLabelText("Год от"), { target: { value: "2020" } });
    fireEvent.click(screen.getByText("Применить"));
    await waitFor(() => {
      const listingCalls = apiMock.mock.calls.map((c) => String(c[0])).filter((p) => p.includes("/listings"));
      expect(listingCalls.some((p) => p.includes("price_max=6750000"))).toBe(true);
      expect(listingCalls.some((p) => p.includes("year_from=2020"))).toBe(true);
    });
    // панель закрылась, на кнопке счётчик активных критериев
    expect(screen.queryByText("Применить")).toBeNull();
    expect(screen.getByText("Фильтры · 2")).toBeTruthy();
  });

  it("«Сбросить» возвращает полную ленту", async () => {
    renderFeed();
    fireEvent.click(screen.getByRole("button", { name: /Фильтры/ }));
    fireEvent.change(screen.getByLabelText("Марка"), { target: { value: "Hyundai" } });
    fireEvent.click(screen.getByText("Применить"));
    await waitFor(() => expect(screen.getByText("Фильтры · 1")).toBeTruthy());
    fireEvent.click(screen.getByRole("button", { name: /Фильтры/ }));
    fireEvent.click(screen.getByText("Сбросить"));
    await waitFor(() => {
      const calls = apiMock.mock.calls.map((c) => String(c[0])).filter((p) => p.includes("/listings"));
      const last = calls[calls.length - 1] ?? "";
      expect(last.includes("make=")).toBe(false);
    });
  });
});

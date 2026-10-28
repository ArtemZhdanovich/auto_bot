/**
 * Тесты «Анализов» (01-ux-audit-ia.md §1.7; тексты — 03 §2.8):
 *  - L2/S6: блока «История поиска» (дубль Сигналов) больше нет;
 *  - verdict-бейджи с SR-текстом вердикта (03 §5.5);
 *  - квота исчерпана (403 quota_exceeded) → апселл «⚡ Безлимит в Pro»;
 *  - L3: гостю — заглушка с замоканным примером вердикта.
 */

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AnalysesScreen } from "./Analyses";
import { api } from "../api/client";
import { isGuest } from "../api/auth";
import { ApiError } from "../api/client";

vi.mock("../api/client", () => ({ api: vi.fn(), ApiError: class extends Error { constructor(public status: number, m: string) { super(m); } } }));
vi.mock("../api/auth", () => ({ isGuest: vi.fn(() => false), haptic: vi.fn() }));
vi.mock("../ui/Toast", () => ({ useToast: () => ({ show: vi.fn() }) }));

const apiMock = vi.mocked(api);

import type { MyAnalysis } from "../api/types";

const analysis = (over: Partial<MyAnalysis> = {}): MyAnalysis => ({
  id: 1,
  listing_id: 7,
  verdict: "deal",
  fair_min: null,
  fair_max: null,
  summary: null,
  created_at: "2026-10-20T10:00:00Z",
  make: "Kia",
  model: "Sorento",
  year: 2022,
  price: 28_900_000,
  currency: "KRW",
  image: null,
  ...over,
});

const renderScreen = async (segment: "views" | "analyses" = "analyses") => {
  render(
    <MemoryRouter initialEntries={["/analyses"]} initialIndex={0}>
      <AnalysesScreen />
    </MemoryRouter>,
  );
  if (segment === "analyses") {
    const btn = await screen.findByRole("tab", { name: "Разборы" });
    btn.click();
  }
};

afterEach(() => {
  cleanup();
  vi.mocked(isGuest).mockReturnValue(false);
  apiMock.mockReset();
});

describe("Analyses: история разборов", () => {
  it("карточки с verdict-бейджем и SR-текстом вердикта; «История поиска» удалена", async () => {
    apiMock.mockResolvedValue([analysis()]);
    renderScreen();
    expect(await screen.findByText("Kia Sorento, 2022")).toBeTruthy();
    // SR-текст вердикта (03 §5.5): цвет дублируется словом
    const badge = document.querySelector(".verdict-badge--deal");
    expect(badge?.getAttribute("aria-label")).toBe("Вердикт: выгодная цена");
    expect(badge?.textContent).toContain("Выгодная цена");
    // S6/L2: дубль Сигналов больше не рендерится
    expect(screen.queryByText("История поиска")).toBeNull();
    expect(screen.queryByText(/Все уведомления/)).toBeNull();
  });

  it("пусто — текст 03 §2.8 + CTA «К ленте»", async () => {
    apiMock.mockResolvedValue([]);
    renderScreen();
    expect(await screen.findByText("Анализов пока нет")).toBeTruthy();
    expect(
      screen.getByText(
        "Откройте объявление в ленте и запустите AI-анализ цены: нейросеть сравнит её с рынком и вынесет вердикт. Все разборы сохранятся здесь.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "К ленте" })).toBeTruthy();
  });

  it("квота исчерпана (403 quota_exceeded) — апселл «Безлимит в Pro», не сырой detail", async () => {
    apiMock.mockRejectedValueOnce(
      new ApiError(403, "quota_exceeded: лимит разборов исчерпан — Free 1 в неделю"),
    );
    renderScreen();
    expect(await screen.findByText("Не удалось загрузить историю")).toBeTruthy();
    expect(screen.getByText(/Бесплатный разбор этой недели уже использован/)).toBeTruthy();
    const cta = screen.getByRole("link", { name: "⚡ Безлимит в Pro" });
    expect(cta.getAttribute("href")).toBe("/profile");
    expect(screen.queryByText(/quota_exceeded/)).toBeNull();
  });

  it("ошибка сети — «Повторить», а не апселл", async () => {
    apiMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    renderScreen();
    expect(await screen.findByText("Не удалось загрузить историю")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Повторить" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "⚡ Безлимит в Pro" })).toBeNull();
  });
});

describe("Analyses: гость (L3)", () => {
  it("заглушка с примером вердикта (−7% к рынку) + «Войти», API не зовётся", async () => {
    vi.mocked(isGuest).mockReturnValue(true);
    await renderScreen();
    expect(screen.getByText("Войдите, чтобы сохранять анализы")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Войти" })).toBeTruthy();
    // замоканный пример вердикта — демонстрация ценности до входа
    expect(document.querySelector(".an-demo")?.textContent).toContain("−7% к рынку");
    expect(apiMock).not.toHaveBeenCalled();
  });
});

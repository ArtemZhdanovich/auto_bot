/**
 * Тесты AI-поиска (01-ux-audit-ia.md §1.4, A1–A4; тексты — 03 §2.5):
 *  A1 — интро с примерами-промптами и карточка «Понял так: …»;
 *  A2 — финализация: «Поиск запущен», источники, «Показать в ленте»
 *       с критериями и «Открыть сигнал»;
 *  A3 — сброс ↺ с подтверждением в шапке, «Сброс» из композера убран;
 *  ошибки API — по маппингу 03 §3.2 (503 → фолбэк «к сигналам»).
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AiSearchScreen } from "./AiSearch";
import { aiFinalize, aiMessage, aiReset } from "../api/ai";
import { ApiError } from "../api/client";

vi.mock("../api/ai", () => ({
  aiMessage: vi.fn(),
  aiFinalize: vi.fn(),
  aiReset: vi.fn(async () => ({ reset: true })),
}));

const aiMessageMock = vi.mocked(aiMessage);
const aiFinalizeMock = vi.mocked(aiFinalize);
const aiResetMock = vi.mocked(aiReset);

// jsdom не умеет scrollIntoView (автоскролл чата к последнему сообщению)
Element.prototype.scrollIntoView = () => undefined;

/** Показывает текущий pathname+search — для проверки переходов A2. */
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname + loc.search}</div>;
}

const renderScreen = () =>
  render(
    <MemoryRouter initialEntries={["/ai"]} initialIndex={0}>
      <LocationProbe />
      <AiSearchScreen />
    </MemoryRouter>,
  );

/** Полный ответ агента (контракт AiReply). */
const reply = (over: Partial<Parameters<typeof aiMessageMock.mockResolvedValueOnce>[0]> = {}) => ({
  session_id: "s1",
  text: "Какой бюджет рассматриваете?",
  options: ["до $30 000", "$30–50 тыс."],
  status: "ok",
  finalize_ready: false,
  turn_count: 1,
  criteria: {},
  ...over,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("AiSearch: интро и карточка критериев (A1)", () => {
  it("интро показывает заголовок и примеры-промпты чипами", () => {
    renderScreen();
    expect(screen.getByText("AI-подбор автомобиля")).toBeTruthy();
    expect(
      screen.getByRole("button", { name: "Hyundai Palisade 2022–2024, дизель, до 45к $" }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Гибрид до $20 000 для города" })).toBeTruthy();
    // история диалога озвучивается скрин-ридером (aria-live)
    expect(document.querySelector(".ai-chat__history")?.getAttribute("aria-live")).toBe("polite");
  });

  it("клик по чипсу-примеру отправляет промпт агенту", async () => {
    aiMessageMock.mockResolvedValueOnce(reply());
    renderScreen();
    fireEvent.click(screen.getByRole("button", { name: "Гибрид до $20 000 для города" }));
    await waitFor(() => expect(aiMessageMock).toHaveBeenCalledWith("Гибрид до $20 000 для города"));
    expect(await screen.findByText("Какой бюджет рассматриваете?")).toBeTruthy();
  });

  it("после ответа бота показывает карточку «Понял так: …» с критериями", async () => {
    aiMessageMock.mockResolvedValueOnce(
      reply({
        text: "Понял задачу!",
        criteria: { make: "Palisade", year_from: 2022, year_to: 2024, fuel: "diesel", price_max: 45000 },
      }),
    );
    renderScreen();
    fireEvent.click(screen.getByRole("button", { name: "Минивэн для семьи, до 100 тыс. км" }));
    const card = await screen.findByText("Понял так:");
    expect(card.parentElement?.textContent).toContain("Palisade");
    expect(card.parentElement?.textContent).toContain("2022–2024");
    expect(card.parentElement?.textContent).toContain("Дизель");
    expect(card.parentElement?.textContent?.replace(/\u00A0/g, " ")).toContain("до $45 000");
  });
});

describe("AiSearch: финализация — не тупик (A2)", () => {
  const criteria = { make: "Palisade", price_max: 45000 };

  it("«Запустить поиск» → источник перечислены, «Показать в ленте» ведёт в /?критерии", async () => {
    aiMessageMock.mockResolvedValueOnce(
      reply({ text: "Всё собрал", finalize_ready: true, options: [], criteria }),
    );
    aiFinalizeMock.mockResolvedValueOnce({ sources: ["encar", "kcar"] });
    renderScreen();
    fireEvent.click(screen.getByRole("button", { name: "Минивэн для семьи, до 100 тыс. км" }));
    fireEvent.click(await screen.findByRole("button", { name: "🔎 Запустить поиск" }));
    expect(await screen.findByText(/Поиск запущен!/)).toBeTruthy();
    // источники перечислены чипсами
    expect(screen.getByText("encar")).toBeTruthy();
    expect(screen.getByText("kcar")).toBeTruthy();
    // сигнал сохранён + переход в ленту с критериями deep-link-формата
    expect(screen.getByText("Сигнал сохранён — новые варианты придут в чат-бот.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Показать в ленте" }));
    expect(screen.getByTestId("loc").textContent).toBe("/?make=Palisade&price_max=45000");
  });

  it("«Открыть сигнал» ведёт в Сигналы; без источников — подсказка про «Сигналы»", async () => {
    aiMessageMock.mockResolvedValueOnce(
      reply({ text: "Готово", finalize_ready: true, options: [], criteria }),
    );
    aiFinalizeMock.mockResolvedValueOnce({ sources: [] });
    renderScreen();
    fireEvent.click(screen.getByRole("button", { name: "Минивэн для семьи, до 100 тыс. км" }));
    fireEvent.click(await screen.findByRole("button", { name: "🔎 Запустить поиск" }));
    expect(
      await screen.findByText(/активных источников пока нет — проверьте подписки/),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Открыть сигнал" }));
    expect(screen.getByTestId("loc").textContent).toBe("/filters");
  });
});

describe("AiSearch: сброс с подтверждением (A3)", () => {
  it("в композере нет «Сброс»; ↺ в шапке спрашивает подтверждение", async () => {
    aiMessageMock.mockResolvedValueOnce(reply());
    renderScreen();
    fireEvent.click(screen.getByRole("button", { name: "Минивэн для семьи, до 100 тыс. км" }));
    await screen.findByText("Какой бюджет рассматриваете?");
    // кнопки «Сброс» в композере больше нет (A3)
    expect(screen.queryByText("Сброс")).toBeNull();
    const openReset = () =>
      document.querySelector('.ai-chat__reset[aria-label="Начать заново"]');
    fireEvent.click(openReset()!);
    // подтверждение: «Отмена» сохраняет диалог, «Да, сбросить» — очищает
    fireEvent.click(screen.getByRole("button", { name: "Отмена" }));
    expect(screen.getByText("Какой бюджет рассматриваете?")).toBeTruthy();
    fireEvent.click(openReset()!);
    fireEvent.click(screen.getByRole("button", { name: "Да, сбросить" }));
    await waitFor(() => expect(aiResetMock).toHaveBeenCalled());
    expect(screen.getByText("AI-подбор автомобиля")).toBeTruthy();
    expect(screen.queryByText("Какой бюджет рассматриваете?")).toBeNull();
  });
});

describe("AiSearch: ошибки агента по маппингу 03 §3.2", () => {
  it("503 → «AI-агент отдыхает» + фолбэк «Настроить уведомления вручную» → /filters", async () => {
    aiMessageMock.mockRejectedValueOnce(new ApiError(503, "AI temporarily unavailable"));
    renderScreen();
    fireEvent.click(screen.getByRole("button", { name: "Минивэн для семьи, до 100 тыс. км" }));
    expect(await screen.findByText(/AI-агент отдыхает/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Настроить уведомления вручную →" }));
    expect(screen.getByTestId("loc").textContent).toBe("/filters");
  });

  it("429 → «Слишком много вопросов подряд» без сырого detail", async () => {
    aiMessageMock.mockRejectedValueOnce(new ApiError(429, "rate limit exceeded"));
    renderScreen();
    fireEvent.click(screen.getByRole("button", { name: "Минивэн для семьи, до 100 тыс. км" }));
    expect(await screen.findByText(/Слишком много вопросов подряд/)).toBeTruthy();
    expect(screen.queryByText(/rate limit/)).toBeNull();
  });
});

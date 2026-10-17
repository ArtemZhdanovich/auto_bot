/** Тесты кнопки «назад» на не-табовых экранах (калькулятор, сравнение). */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CalcScreen } from "./Calc";
import { CompareScreen } from "./Compare";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: vi.fn(async () => ({ components: [] })) }));
vi.mock("../api/auth", () => ({ haptic: vi.fn() }));

/** Показывает текущий pathname — чтобы проверить navigate(-1). */
function LocationProbe() {
  const loc = useLocation();
  return <div data-testid="loc">{loc.pathname}</div>;
}

const renderWithHistory = (ui: React.ReactNode) =>
  render(
    <MemoryRouter initialEntries={["/", "/calc"]} initialIndex={1}>
      <LocationProbe />
      {ui}
    </MemoryRouter>,
  );

afterEach(() => cleanup());

describe("BackButton на экранах расчётов", () => {
  it("Calc: кнопка «назад» есть и возвращает на предыдущий экран", () => {
    renderWithHistory(<CalcScreen />);
    const btn = document.querySelector(".screen-back");
    expect(btn).not.toBeNull();
    expect(btn?.getAttribute("aria-label")).toBe("Назад");
    expect(screen.getByTestId("loc").textContent).toBe("/calc");
    fireEvent.click(btn!);
    expect(screen.getByTestId("loc").textContent).toBe("/");
  });

  it("Compare: кнопка «назад» есть и возвращает на предыдущий экран", () => {
    renderWithHistory(<CompareScreen />);
    const btn = document.querySelector(".screen-back");
    expect(btn).not.toBeNull();
    fireEvent.click(btn!);
    expect(screen.getByTestId("loc").textContent).toBe("/");
  });
});

const apiMock = vi.mocked(api);

/** Полный мок-ответ бэка (region/currency/local — новые поля юрисдикции). */
const byResult = {
  price_usd: 18000,
  engine_cc: 2000,
  year: 2022,
  age_years: 4,
  age_group: "3–5 лет",
  region: "by",
  currency: "Br",
  util_category: "льготный",
  util_note: "",
  components: [{ label: "Авто по счёту", usd: 18000, rub: 54900, local: 54900 }],
  legal_refs: [{ label: "Пошлины", ref: "Решение Совета ЕЭК № 54" }],
  total_rub: 54900,
  total_usd: 18000,
  total_local: 54900,
};

describe("Calc: юрисдикция расчёта по валюте (BYN → РБ)", () => {
  it("переключатель «Расчёт по РБ» отправляет region=by и показывает Br", async () => {
    apiMock.mockResolvedValueOnce(byResult);
    render(
      <MemoryRouter initialEntries={["/calc"]} initialIndex={0}>
        <CalcScreen />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByRole("button", { name: "🇧🇾 Расчёт по РБ" }));
    fireEvent.change(screen.getByPlaceholderText("18 000"), { target: { value: "18000" } });
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать" }));
    await screen.findByText(/Итого «под ключ» \(РБ\)/);
    const call = apiMock.mock.calls.at(-1)!;
    const body = JSON.parse((call[1] as RequestInit).body as string);
    expect(body.region).toBe("by");
    // итог и строка компонента — в BYN
    expect(screen.getAllByText(/54 900/).length).toBeGreaterThanOrEqual(2);
    const total = document.querySelector(".calc-total__value");
    // toLocaleString("ru-RU") даёт NBSP-разделитель тысяч
    expect(total?.textContent?.replace(/\u00A0/g, " ")).toBe("54 900 Br");
  });

  it("deep-link ?cur=BYN сразу считает по РБ", async () => {
    apiMock.mockResolvedValueOnce(byResult);
    render(
      <MemoryRouter initialEntries={["/calc?price=18000&year=2022&cur=BYN"]} initialIndex={0}>
        <CalcScreen />
      </MemoryRouter>,
    );
    await screen.findByText(/Итого «под ключ» \(РБ\)/);
    const body = JSON.parse((apiMock.mock.calls[0]![1] as RequestInit).body as string);
    expect(body.region).toBe("by");
  });
});

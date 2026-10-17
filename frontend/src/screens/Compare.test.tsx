/** Тесты Compare: юрисдикция сравнения (регион-чипы → region в запросе, итог в местной валюте). */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CompareScreen } from "./Compare";
import { api } from "../api/client";

vi.mock("../api/client", () => ({ api: vi.fn(async () => ({ items: [] })) }));
vi.mock("../api/auth", () => ({ haptic: vi.fn() }));

const apiMock = vi.mocked(api);

const compareOut = {
  items: [
    { title: "Вариант 1", price_usd: 15000, total_usd: 24000, total_rub: 2208000, total_local: 73200, currency: "Br", age_group: "3–5 лет" },
    { title: "Вариант 2", price_usd: 20000, total_usd: 26000, total_rub: 2392000, total_local: 79300, currency: "Br", age_group: "3–5 лет" },
  ],
};

const renderCompare = () =>
  render(
    <MemoryRouter initialEntries={["/compare"]} initialIndex={0}>
      <CompareScreen />
    </MemoryRouter>,
  );

afterEach(() => {
  cleanup();
  localStorage.clear(); // первый тест сохраняет region=by
  apiMock.mockClear();
});

describe("Compare: юрисдикция по региону", () => {
  it("чип «Беларусь» → region=by в запросе, итог в Br", async () => {
    apiMock.mockResolvedValueOnce(compareOut);
    renderCompare();
    fireEvent.click(screen.getByRole("button", { name: "🇧🇾 Беларусь" }));
    fireEvent.change(screen.getAllByPlaceholderText("18 000")[0]!, { target: { value: "15000" } });
    fireEvent.change(screen.getAllByPlaceholderText("2022")[0]!, { target: { value: "2021" } });
    fireEvent.change(screen.getAllByPlaceholderText("18 000")[1]!, { target: { value: "20000" } });
    fireEvent.change(screen.getAllByPlaceholderText("2022")[1]!, { target: { value: "2022" } });
    fireEvent.click(screen.getByRole("button", { name: "Сравнить" }));
    await screen.findByText(/лучший итог/);
    const body = JSON.parse((apiMock.mock.calls[0]![1] as RequestInit).body as string);
    expect(body.items[0].region).toBe("by");
    expect(body.items[1].region).toBe("by");
    // итоги в местной валюте
    expect(screen.getAllByText(/Br/).length).toBeGreaterThanOrEqual(2);
  });

  it("дефолт — регион РФ (region=ru), итог в ₽", async () => {
    apiMock.mockResolvedValueOnce({
      items: compareOut.items.map((i) => ({ ...i, currency: "₽", total_local: i.total_rub })),
    });
    renderCompare();
    fireEvent.change(screen.getAllByPlaceholderText("18 000")[0]!, { target: { value: "15000" } });
    fireEvent.change(screen.getAllByPlaceholderText("2022")[0]!, { target: { value: "2021" } });
    fireEvent.change(screen.getAllByPlaceholderText("18 000")[1]!, { target: { value: "20000" } });
    fireEvent.change(screen.getAllByPlaceholderText("2022")[1]!, { target: { value: "2022" } });
    fireEvent.click(screen.getByRole("button", { name: "Сравнить" }));
    await screen.findByText(/лучший итог/);
    const body = JSON.parse((apiMock.mock.calls[0]![1] as RequestInit).body as string);
    expect(body.items[0].region).toBe("ru");
  });
});

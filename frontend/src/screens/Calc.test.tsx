/** Тесты кнопки «назад» на не-табовых экранах (калькулятор, сравнение). */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CalcScreen } from "./Calc";
import { CompareScreen } from "./Compare";

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

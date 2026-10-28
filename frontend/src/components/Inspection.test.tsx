/** Тесты секции осмотра: карта повреждений, чек-лист, интерактив зон. */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { InspectionSection } from "./Inspection";
import { ZONE_LABEL, type InspectionReport } from "../lib/inspection";

afterEach(cleanup);

const report: InspectionReport = {
  source: "manual",
  inspector: "Иван",
  inspected_at: "2026-10-17T10:00:00Z",
  grade: "good",
  summary: "Машина в хорошем состоянии",
  marks: [
    { zone: "trunk", type: "scratch", note: "скол на кромке", pos: [120, 235] },
    { zone: "door_rl", type: "dent", pos: [33, 165] },
  ],
  items: [
    { key: "engine", status: "ok" },
    { key: "brakes", status: "warn", note: "передние колодки" },
  ],
};

describe("InspectionSection", () => {
  it("показывает оценку, метаданные и чек-лист", () => {
    render(<InspectionSection report={report} />);
    expect(screen.getByText(/Хорошее/)).toBeTruthy();
    expect(screen.getByText(/Иван/)).toBeTruthy();
    expect(screen.getByText("Чек-лист")).toBeTruthy();
    expect(screen.getByText("Двигатель")).toBeTruthy();
    expect(screen.getByText("Тормозная система")).toBeTruthy();
    expect(screen.getByText("передние колодки")).toBeTruthy();
    expect(screen.getByText(/Машина в хорошем состоянии/)).toBeTruthy();
  });

  it("рисует маркеры повреждений на диаграммах Encar", () => {
    const { container } = render(<InspectionSection report={report} />);
    // Две диаграммы (внешние панели + силовая структура)
    expect(container.querySelectorAll(".inspection__diagram").length).toBe(2);
    // Маркеры — по одному на зону с повреждением
    expect(container.querySelectorAll(".inspection__pin").length).toBe(2);
    // Легенда: 9 типов повреждений (включая repair/frame из классического чека)
    expect(container.querySelectorAll(".inspection__legend-item").length).toBe(9);
  });

  it("тап по маркеру открывает детали повреждения", () => {
    const { container } = render(<InspectionSection report={report} />);
    expect(container.querySelector(".inspection__zone-title")).toBeNull();
    const pins = container.querySelectorAll(".inspection__pin");
    fireEvent.click(pins[0]);
    expect(
      container.querySelector(".inspection__zone-title")?.textContent,
    ).toContain(ZONE_LABEL.trunk);
    const marks = container.querySelectorAll(".inspection__zone-mark");
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toContain("Царапина");
    expect(marks[0].textContent).toContain("скол на кромке");
    // закрытие
    fireEvent.click(screen.getByLabelText("Закрыть"));
    expect(container.querySelector(".inspection__zone-title")).toBeNull();
  });

  // ---- Новые состояния: карта не исчезает при пустых marks (encar) ----

  it("при пустых marks карта остаётся: диаграммы без пинов + зелёная плашка", () => {
    const { container } = render(
      <InspectionSection report={{ ...report, marks: [] }} />,
    );
    // Обе диаграммы Encar на месте — секция не пропала
    expect(container.querySelectorAll(".inspection__diagram").length).toBe(2);
    // Пинов нет, легенды цветов тоже (нечего объяснять)
    expect(container.querySelectorAll(".inspection__pin").length).toBe(0);
    expect(container.querySelectorAll(".inspection__legend-item").length).toBe(0);
    // Плашка объясняет, почему пинов нет
    expect(
      screen.getByText(/Повреждений по чеку не зафиксировано/),
    ).toBeTruthy();
    // Чек-лист при этом рендерится
    expect(screen.getByText("Чек-лист")).toBeTruthy();
    expect(screen.getByText("Двигатель")).toBeTruthy();
  });

  it("marks и items пустые — карта с плашкой и хотя бы сводка (факты)", () => {
    const { container } = render(
      <InspectionSection
        report={{ ...report, marks: [], items: [], summary: "Кузов в порядке · салон чистый" }}
      />,
    );
    expect(container.querySelectorAll(".inspection__diagram").length).toBe(2);
    expect(screen.getByText(/Повреждений по чеку не зафиксировано/)).toBeTruthy();
    // Сводка не потерялась: факты из summary списком
    expect(screen.getByText("Кузов в порядке")).toBeTruthy();
    expect(screen.getByText("салон чистый")).toBeTruthy();
  });
});

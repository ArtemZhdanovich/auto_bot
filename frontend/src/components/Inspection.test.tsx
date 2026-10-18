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
    { zone: "front_bumper", type: "scratch", note: "скол на кромке" },
    { zone: "door_rl", type: "dent" },
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

  it("подсвечивает зоны с дефектами на SVG-схеме", () => {
    const { container } = render(<InspectionSection report={report} />);
    const marked = container.querySelectorAll(".inspection__zone--mark");
    expect(marked.length).toBe(2);
    // Легенда: 7 типов повреждений
    expect(container.querySelectorAll(".inspection__legend-item").length).toBe(7);
  });

  it("тап по зоне открывает детали повреждения", () => {
    const { container } = render(<InspectionSection report={report} />);
    // SVG <title> тоже содержит название зоны — проверяем именно панель деталей
    expect(container.querySelector(".inspection__zone-title")).toBeNull();
    const paths = document.querySelectorAll(".inspection__zone--mark");
    fireEvent.click(paths[0]);
    expect(
      container.querySelector(".inspection__zone-title")?.textContent,
    ).toContain(ZONE_LABEL.front_bumper);
    const marks = container.querySelectorAll(".inspection__zone-mark");
    expect(marks.length).toBe(1);
    expect(marks[0].textContent).toContain("Царапина");
    expect(marks[0].textContent).toContain("скол на кромке");
    // закрытие
    fireEvent.click(screen.getByLabelText("Закрыть"));
    expect(container.querySelector(".inspection__zone-title")).toBeNull();
  });
});

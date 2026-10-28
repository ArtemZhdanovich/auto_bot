/** Тосты: одинаковые подряд не дублируются, разные показываются оба. */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { ToastProvider, useToast } from "./Toast";

afterEach(() => cleanup());

function Harness() {
  const toast = useToast();
  return (
    <>
      <button
        onClick={() => {
          toast.show("Добавлено в избранное");
          toast.show("Добавлено в избранное");
        }}
      >
        Двойной тап
      </button>
      <button
        onClick={() => {
          toast.show("Первый");
          toast.show("Второй", { error: true });
        }}
      >
        Разные
      </button>
    </>
  );
}

describe("Toast: дедупликация", () => {
  it("два одинаковых тоста подряд показываются одним", () => {
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Двойной тап" }));
    expect(screen.getAllByText("Добавлено в избранное").length).toBe(1);
  });

  it("разные тосты показываются оба (дедуп не съедает уникальные)", () => {
    render(
      <ToastProvider>
        <Harness />
      </ToastProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Разные" }));
    expect(screen.getByText("Первый")).toBeTruthy();
    expect(document.querySelector(".toast--error")?.textContent).toBe("Второй");
  });
});

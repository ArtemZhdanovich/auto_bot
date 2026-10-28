/** Модалка «Нужен вход»: закрывается по Esc и клику по фону (03 §5.3). */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it } from "vitest";

import { LoginPromptProvider, useLoginPrompt } from "./LoginPrompt";

afterEach(() => cleanup());

function Harness() {
  const prompt = useLoginPrompt();
  return <button onClick={() => prompt.show("Текст причины входа")}>Показать</button>;
}

const renderModal = () =>
  render(
    <MemoryRouter>
      <LoginPromptProvider>
        <Harness />
      </LoginPromptProvider>
    </MemoryRouter>,
  );

describe("LoginPrompt: закрытие модалки", () => {
  it("Esc закрывает открытую модалку (как шит ленты и лайтбокс)", () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "Показать" }));
    expect(screen.getByText("Текст причины входа")).toBeTruthy();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByText("Текст причины входа")).toBeNull();
  });

  it("клик по фону закрывает; Esc без открытой модалки ничего не ломает", () => {
    renderModal();
    fireEvent.click(screen.getByRole("button", { name: "Показать" }));
    fireEvent.click(document.querySelector(".modal")!);
    expect(screen.queryByText("Текст причины входа")).toBeNull();
    // Esc по закрытому провайдеру — no-op
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.getByRole("button", { name: "Показать" })).toBeTruthy();
  });
});

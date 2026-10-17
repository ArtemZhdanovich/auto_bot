/** Тесты экрана входа: кнопка «Войти через Telegram», ссылка на бота, ожидание. */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LoginScreen } from "./Login";
import { createTelegramLoginLink, pollTelegramLogin } from "../api/auth";

vi.mock("../api/auth", () => ({
  createTelegramLoginLink: vi.fn(),
  pollTelegramLogin: vi.fn(),
  webLogin: vi.fn(),
  webRegister: vi.fn(),
}));

const linkMock = vi.mocked(createTelegramLoginLink);
const pollMock = vi.mocked(pollTelegramLogin);

const renderLogin = () =>
  render(
    <MemoryRouter>
      <LoginScreen onAuthed={() => {}} />
    </MemoryRouter>,
  );

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("LoginScreen — вход через Telegram", () => {
  it("показывает кнопку входа через Telegram", () => {
    renderLogin();
    expect(screen.getByText("Войти через Telegram")).toBeTruthy();
  });

  it("по клику открывает ссылку на бота из ответа API и ждёт подтверждения", async () => {
    linkMock.mockResolvedValue({
      code: "abc123",
      url: "https://t.me/mybot?start=login_abc123",
      expires_in: 600,
    });
    pollMock.mockResolvedValue({ status: "pending" });
    const opened: string[] = [];
    vi.stubGlobal("open", (url: string) => {
      opened.push(url);
      return null;
    });

    renderLogin();
    fireEvent.click(screen.getByText("Войти через Telegram"));

    await waitFor(() => {
      expect(opened).toEqual(["https://t.me/mybot?start=login_abc123"]);
    });
    expect(screen.getByText(/Подтвердите вход в Telegram/)).toBeTruthy();
    // повторный запрос ссылки — только кнопка «Отменить», новой ссылки нет
    expect(screen.getByText("Отменить")).toBeTruthy();
    expect(screen.queryByText("Войти через Telegram")).toBeNull();
  });

  it("после подтверждения в боте вызывает onAuthed", async () => {
    vi.useFakeTimers();
    linkMock.mockResolvedValue({
      code: "abc123",
      url: "https://t.me/mybot?start=login_abc123",
      expires_in: 600,
    });
    pollMock.mockResolvedValue({ status: "pending" });
    vi.stubGlobal("open", () => null);
    const onAuthed = vi.fn();

    render(
      <MemoryRouter>
        <LoginScreen onAuthed={onAuthed} />
      </MemoryRouter>,
    );
    fireEvent.click(screen.getByText("Войти через Telegram"));
    await vi.waitFor(() => expect(linkMock).toHaveBeenCalled());

    pollMock.mockResolvedValue({
      access_token: "a",
      refresh_token: "r",
      token_type: "bearer",
      user: {
        telegram_id: 1,
        username: null,
        first_name: "Иван",
        last_name: null,
        language_code: "ru",
        role: "buyer",
        subscription_tier: "free",
        subscription_until: null,
        created_at: "2026-01-01T00:00:00Z",
        is_active: true,
        onboarded_at: null,
      },
    });
    await vi.advanceTimersByTimeAsync(2600);
    expect(onAuthed).toHaveBeenCalled();
    vi.useRealTimers();
  });
});

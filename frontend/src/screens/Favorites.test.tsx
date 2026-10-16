import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { AppRoot } from "@telegram-apps/telegram-ui";
import { afterEach, describe, expect, it, vi } from "vitest";

import { FavoritesScreen } from "./Favorites";
import { isGuest } from "../api/auth";

const { promptShow } = vi.hoisted(() => ({ promptShow: vi.fn() }));

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../api/auth", () => ({ isGuest: vi.fn(() => false) }));
vi.mock("../ui/LoginPrompt", () => ({ useLoginPrompt: () => ({ show: promptShow }) }));

afterEach(() => cleanup());

const renderScreen = () =>
  render(
    <AppRoot>
      <MemoryRouter>
        <FavoritesScreen />
      </MemoryRouter>
    </AppRoot>,
  );

describe("FavoritesScreen", () => {
  it("гость: экран-гейт «Нужен вход» + кнопка «Войти», модалка и API не вызываются", async () => {
    vi.mocked(isGuest).mockReturnValue(true);
    const { api } = await import("../api/client");
    renderScreen();
    expect(screen.getByText("Нужен вход")).toBeTruthy();
    expect(document.body.textContent).toContain("Войти");
    // Модалка с просьбой входа показана сразу при открытии раздела
    expect(promptShow).toHaveBeenCalledWith(
      "Избранное доступно после входа — так машины сохраняются за вашим аккаунтом.",
    );
    expect(api).not.toHaveBeenCalled();
    vi.mocked(isGuest).mockReturnValue(false);
  });

  it("не гость: грузит список избранного", async () => {
    vi.mocked(isGuest).mockReturnValue(false);
    const { api } = await import("../api/client");
    vi.mocked(api).mockResolvedValue([
      {
        id: 7,
        source: "kcar",
        source_id: "X7",
        url: "https://example.com/7",
        make: "Hyundai",
        model: "Staria",
        year: 2023,
        price: 30_000_000,
        currency: "KRW",
        mileage_km: 10_000,
        vin: null,
        images: [],
        parsed_at: "2026-10-01T00:00:00Z",
        is_active: true,
        first_seen_at: null,
        first_price: null,
        price_history: null,
        title_raw: null,
        title_ru: null,
      },
    ]);
    renderScreen();
    await screen.findByText("Избранное: 1");
    expect(screen.getByText("Hyundai Staria")).toBeTruthy();
  });
});

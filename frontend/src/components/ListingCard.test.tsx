/** Компонентные тесты карточки объявления (jsdom + testing-library). */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ListingCard } from "./ListingCard";
import { isGuest } from "../api/auth";
import type { Listing } from "../api/types";

const { promptShow } = vi.hoisted(() => ({ promptShow: vi.fn() }));

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../api/auth", () => ({ haptic: vi.fn(), isGuest: vi.fn(() => false) }));
vi.mock("../ui/Toast", () => ({ useToast: () => ({ show: vi.fn() }) }));
vi.mock("../ui/LoginPrompt", () => ({ useLoginPrompt: () => ({ show: promptShow }) }));

const base: Listing = {
  id: 1,
  source: "kcar",
  source_id: "X1",
  url: "https://example.com/1",
  make: "Kia",
  model: "Carnival",
  year: 2022,
  price: 18_400_000,
  currency: "KRW",
  mileage_km: 42_000,
  vin: null,
  images: ["https://img/1.jpg", "https://img/2.jpg"],
  parsed_at: "2026-10-01T00:00:00Z",
  is_active: true,
  first_seen_at: null,
  first_price: null,
  price_history: null,
  title_raw: null,
  title_ru: null,
};

afterEach(() => cleanup());

const renderCard = (listing: Listing, favorited = false) =>
  render(
    <MemoryRouter>
      <ListingCard listing={listing} favorited={favorited} />
    </MemoryRouter>,
  );

describe("ListingCard", () => {
  it("рисует заголовок, цену и счётчик фото в карточке-контейнере", () => {
    localStorage.setItem("autopick_region", "ru"); // детерминированный регион
    renderCard(base);
    const card = document.querySelector(".listing-card");
    expect(card).not.toBeNull();
    expect(screen.getByText("Kia Carnival, 2022")).toBeTruthy();
    // В ленте — только валюта региона: 18.4 млн KRW -> $13 630 -> 1 254 000 RUB
    expect(
      document.querySelector(".listing-card__price")?.textContent?.replace(/\s/g, " "),
    ).toContain("1 254 000");
    // $ и цена источника (KRW) из ленты убраны — они остаются на PDP
    expect(document.querySelector(".listing-card__price")?.textContent).not.toContain("$");
    expect(document.querySelector(".listing-card__orig")).toBeNull();
    expect(document.querySelector(".listing-card__price")?.textContent).not.toContain("KRW");
    expect(document.querySelector(".listing-card__photos-count")?.textContent).toBe("2");
  });

  it("make/model null → «Без названия», а не «null undefined»", () => {
    renderCard({ ...base, make: null, model: null });
    expect(screen.getByText("Без названия, 2022")).toBeTruthy();
  });

  it("цена снизилась → бейдж с процентом", () => {
    renderCard({ ...base, first_price: 20_000_000 });
    expect(document.querySelector(".listing-card__badge--drop")?.textContent).toContain("−8%");
  });

  it("объявление исчезло → бейдж «Исчезло» вместо бейджа цены", () => {
    renderCard({ ...base, is_active: false, first_price: 20_000_000 });
    expect(document.querySelector(".listing-card__badge--gone")).not.toBeNull();
    expect(document.querySelector(".listing-card__badge--drop")).toBeNull();
  });

  it("кнопка избранного — переключатель с aria-pressed", () => {
    renderCard(base);
    const btn = document.querySelector(".listing-card__fav") as HTMLButtonElement;
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    render(
      <MemoryRouter>
        <ListingCard listing={base} favorited />
      </MemoryRouter>,
    );
    expect(
      (document.querySelectorAll(".listing-card__fav")[1] as HTMLButtonElement).getAttribute(
        "aria-pressed",
      ),
    ).toBe("true");
  });

  it("гость: клик по ❤ показывает модалку входа и не дёргает API", async () => {
    vi.mocked(isGuest).mockReturnValue(true);
    const { api } = await import("../api/client");
    renderCard(base);
    fireEvent.click(document.querySelector(".listing-card__fav") as HTMLButtonElement);
    expect(promptShow).toHaveBeenCalledWith("Войдите, чтобы сохранять машины в избранное.");
    expect(api).not.toHaveBeenCalled();
    vi.mocked(isGuest).mockReturnValue(false);
  });

  it("не гость: клик по ❤ вызывает API и переключает сердечко", async () => {
    vi.mocked(isGuest).mockReturnValue(false);
    const { api } = await import("../api/client");
    vi.mocked(api).mockResolvedValue({ favorited: true });
    renderCard(base);
    promptShow.mockClear();
    fireEvent.click(document.querySelector(".listing-card__fav") as HTMLButtonElement);
    await vi.waitFor(() => {
      expect(
        (document.querySelector(".listing-card__fav") as HTMLButtonElement).getAttribute(
          "aria-pressed",
        ),
      ).toBe("true");
    });
    expect(api).toHaveBeenCalledWith("/listings/1/favorite", { method: "POST" });
    expect(promptShow).not.toHaveBeenCalled();
  });
});

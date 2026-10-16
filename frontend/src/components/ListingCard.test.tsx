/** Компонентные тесты карточки объявления (jsdom + testing-library). */
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ListingCard } from "./ListingCard";
import type { Listing } from "../api/types";

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../api/auth", () => ({ haptic: vi.fn() }));
vi.mock("../ui/Toast", () => ({ useToast: () => ({ show: vi.fn() }) }));

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
    renderCard(base);
    const card = document.querySelector(".listing-card");
    expect(card).not.toBeNull();
    expect(screen.getByText("Kia Carnival, 2022")).toBeTruthy();
    // toLocaleString("ru") даёт неразрывные пробелы — нормализуем
    expect(
      document.querySelector(".listing-card__price")?.textContent?.replace(/ /g, " "),
    ).toContain("18 400 000");
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
});

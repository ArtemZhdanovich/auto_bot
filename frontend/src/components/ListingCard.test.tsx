/** Компонентные тесты карточки объявления (jsdom + testing-library).
 *  Новая структура по редизайну (01-ux-audit F6–F9): $ + ≈₽, источник
 *  текстом в метастроке (без ссылки), бейдж «чек», максимум 3 спека. */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ListingCard } from "./ListingCard";
import { isGuest } from "../api/auth";
import type { Listing } from "../api/types";

const { promptShow } = vi.hoisted(() => ({ promptShow: vi.fn() }));

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../api/auth", () => ({
  haptic: vi.fn(),
  isGuest: vi.fn(() => false),
  getWebApp: vi.fn(() => null),
}));
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
    <MemoryRouter initialEntries={["/"]}>
      <Routes>
        <Route path="/" element={<ListingCard listing={listing} favorited={favorited} />} />
        <Route path="/listing/:id" element={<div>PDP-OPENED</div>} />
      </Routes>
    </MemoryRouter>,
  );

describe("ListingCard (редизайн: F6–F9)", () => {
  it("заголовок make model, метастрока с источником ТЕКСТОМ и две цены (F6/F9)", () => {
    localStorage.setItem("autopick_region", "ru"); // детерминированный регион
    renderCard(base);
    const card = document.querySelector(".lcard");
    expect(card).not.toBeNull();
    expect(screen.getByText("Kia Carnival")).toBeTruthy();
    // Метастрока «2022 · 42 000 км · Корея · KCar»: источник текстом, без ↗
    // (\s — включая NBSP из toLocaleString("ru-RU"))
    const meta = (document.querySelector(".lcard__meta")?.textContent ?? "").replace(
      /\s/g,
      " ",
    );
    expect(meta).toContain("2022");
    expect(meta).toContain("42 000 км");
    expect(meta).toContain("Корея");
    expect(meta).toContain("KCar");
    expect(card?.textContent).not.toContain("↗");
    // Ссылки на источник в карточке нет вообще (F9): единственная кнопка — ♥
    expect(card?.querySelector("a")).toBeNull();
    // F6: две строки цены — $ (18/700) и ≈ валюта региона (caption)
    // 18.4 млн KRW → $13 630 → ≈ 1 254 000 ₽ (форматтеры lib/fx.ts)
    expect(
      (document.querySelector(".lcard__price")?.textContent ?? "").replace(/\s/g, " "),
    ).toBe("$13 630");
    expect(
      (document.querySelector(".lcard__price-alt")?.textContent ?? "").replace(/\s/g, " "),
    ).toBe("≈ 1 254 000 ₽");
    expect(document.querySelector(".lcard__count")?.textContent).toBe("2");
  });

  it("make/model null → «Без названия», а не «null undefined»", () => {
    renderCard({ ...base, make: null, model: null });
    expect(screen.getByText("Без названия")).toBeTruthy();
  });

  it("цена недоступна → «—», второй строки цены нет", () => {
    renderCard({ ...base, price: null, currency: null });
    expect(document.querySelector(".lcard__price")?.textContent).toBe("—");
    expect(document.querySelector(".lcard__price-alt")).toBeNull();
  });

  it("has_inspection → бейдж «🧾 Чек» (F7)", () => {
    renderCard({ ...base, has_inspection: true } as Listing & { has_inspection?: boolean });
    expect(document.querySelector(".lcard__badge--check")?.textContent).toContain("Чек");
  });

  it("has_inspection нет в ответе → бейджа чека нет", () => {
    renderCard(base);
    expect(document.querySelector(".lcard__badge--check")).toBeNull();
  });

  it("цена снизилась → бейдж с процентом (⬇️)", () => {
    renderCard({ ...base, first_price: 20_000_000 });
    expect(document.querySelector(".lcard__badge--drop")?.textContent).toContain("−8%");
  });

  it("лот неактивен → бейдж «🔴 Исчезло» (рядом с остальными бейджами)", () => {
    renderCard({
      ...base,
      is_active: false,
      first_price: 20_000_000,
      has_inspection: true,
    } as Listing & { has_inspection?: boolean });
    expect(document.querySelector(".lcard__badge--gone")).not.toBeNull();
    expect(document.querySelector(".lcard__badge--drop")).not.toBeNull();
    expect(document.querySelector(".lcard__badge--check")).not.toBeNull();
  });

  it("максимум 3 спека (F8): объём·топливо·КПП, привод не влез — VIN и цвета нет", () => {
    renderCard({
      ...base,
      engine_volume: 2.2,
      fuel: "diesel",
      transmission: "auto",
      drive: "awd",
      color: "black",
      vin: "KNDMB231678901234",
    });
    const specs = document.querySelector(".lcard__specs")?.textContent ?? "";
    expect(specs).toContain("2.2 л");
    expect(specs).toContain("Дизель");
    expect(specs).toContain("Автомат");
    expect(specs).not.toContain("Полный"); // 4-й спек обрезан
    expect(specs).not.toContain("VIN");
    expect(specs).not.toContain("Чёрный");
  });

  it("нет фото → заглушка-силуэт с aria-label «Фото недоступно»", () => {
    renderCard({ ...base, images: [] });
    const ph = document.querySelector(".lcard__ph");
    expect(ph?.getAttribute("aria-label")).toBe("Фото недоступно");
    expect(ph?.querySelector("svg")).not.toBeNull();
    expect(document.querySelector(".lcard__count")).toBeNull();
  });

  it("фото: skeleton пока грузится, исчезает после load", () => {
    renderCard(base);
    expect(document.querySelector(".lcard__img-skel")).not.toBeNull();
    fireEvent.load(document.querySelector(".lcard__img") as HTMLImageElement);
    expect(document.querySelector(".lcard__img-skel")).toBeNull();
  });

  it("фото не загрузилось → силуэт вместо битой картинки", () => {
    renderCard(base);
    fireEvent.error(document.querySelector(".lcard__img") as HTMLImageElement);
    expect(document.querySelector(".lcard__img")).toBeNull();
    expect(document.querySelector(".lcard__ph")?.getAttribute("aria-label")).toBe(
      "Фото недоступно",
    );
  });

  it("aria-label карточки: модель, год, цена — для скринридеров", () => {
    localStorage.setItem("autopick_region", "ru");
    renderCard(base);
    expect(
      (document.querySelector(".lcard")?.getAttribute("aria-label") ?? "").replace(
        /\s/g,
        " ",
      ),
    ).toBe("Kia Carnival, 2022, $13 630");
  });

  it("тап по карточке и Enter/Space ведут на /listing/:id", () => {
    renderCard(base);
    fireEvent.click(document.querySelector(".lcard") as HTMLElement);
    expect(screen.getByText("PDP-OPENED")).toBeTruthy();
  });

  it("Enter открывает карточку с клавиатуры (доступность)", () => {
    renderCard(base);
    fireEvent.keyDown(document.querySelector(".lcard") as HTMLElement, { key: "Enter" });
    expect(screen.getByText("PDP-OPENED")).toBeTruthy();
  });

  it("Space открывает карточку с клавиатуры (03 §5.2)", () => {
    renderCard(base);
    fireEvent.keyDown(document.querySelector(".lcard") as HTMLElement, { key: " " });
    expect(screen.getByText("PDP-OPENED")).toBeTruthy();
  });

  it("кнопка избранного — переключатель с aria-pressed", () => {
    renderCard(base);
    const btn = document.querySelector(".lcard__fav") as HTMLButtonElement;
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    render(
      <MemoryRouter>
        <ListingCard listing={base} favorited />
      </MemoryRouter>,
    );
    expect(
      (document.querySelectorAll(".lcard__fav")[1] as HTMLButtonElement).getAttribute(
        "aria-pressed",
      ),
    ).toBe("true");
  });

  it("гость: клик по ❤ показывает модалку входа и не дёргает API", async () => {
    vi.mocked(isGuest).mockReturnValue(true);
    const { api } = await import("../api/client");
    renderCard(base);
    fireEvent.click(document.querySelector(".lcard__fav") as HTMLButtonElement);
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
    fireEvent.click(document.querySelector(".lcard__fav") as HTMLButtonElement);
    await vi.waitFor(() => {
      expect(
        (document.querySelector(".lcard__fav") as HTMLButtonElement).getAttribute(
          "aria-pressed",
        ),
      ).toBe("true");
    });
    expect(api).toHaveBeenCalledWith("/listings/1/favorite", { method: "POST" });
    expect(promptShow).not.toHaveBeenCalled();
  });

  it("клик по карточке не открывает источник (ссылки больше нет — F9)", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    renderCard(base);
    fireEvent.click(document.querySelector(".lcard") as HTMLElement);
    expect(openSpy).not.toHaveBeenCalled();
    expect(screen.getByText("PDP-OPENED")).toBeTruthy();
    openSpy.mockRestore();
  });
});

describe("ListingCard: дизайн-пасс (02-design-system §7.3)", () => {
  it("бейдж чека — ПОВЕРХ ФОТО (внутри .lcard__media), пилюля не растягивает карточку", () => {
    renderCard({ ...base, has_inspection: true } as Listing & { has_inspection?: boolean });
    const badge = document.querySelector(".lcard__badge--check");
    expect(badge).not.toBeNull();
    // бейдж живёт в слое фото (absolute внутри .lcard__media), не в теле
    expect(badge?.closest(".lcard__media")).not.toBeNull();
    expect(badge?.closest(".lcard__body")).toBeNull();
  });

  it("цена — tabular-nums: класс .tabular на основной и второй строке цены", () => {
    localStorage.setItem("autopick_region", "ru"); // детерминированный регион
    renderCard(base);
    expect(document.querySelector(".lcard__price")?.classList.contains("tabular")).toBe(true);
    expect(document.querySelector(".lcard__price-alt")?.classList.contains("tabular")).toBe(true);
  });

  it("модель — ellipsis максимум в 2 строки (класс clamp на заголовке)", () => {
    renderCard(base);
    const title = document.querySelector(".lcard__title");
    expect(title?.classList.contains("lcard__title--clamp")).toBe(true);
  });
});

describe("ListingCard: бейдж страны (GEO-V1 §3)", () => {
  const metaText = () =>
    (document.querySelector(".lcard__meta")?.textContent ?? "").replace(/\s/g, " ");

  it("внешний лот: флаг + ISO2 рядом с источником («🇨🇳 CN · che168»)", () => {
    renderCard({ ...base, source: "che168", country: "CN" });
    const meta = metaText();
    expect(meta).toContain("🇨🇳 CN");
    expect(meta).toContain("che168");
  });

  it("owner-лот с городом: флаг + город вместо ISO2 («🇷🇺 Москва»)", () => {
    renderCard({ ...base, owner_type: "user", country: "RU", city: "Москва" });
    const meta = metaText();
    expect(meta).toContain("🇷🇺 Москва");
    // код страны у owner-лота не дублируется — гео читается городом
    expect(meta).not.toMatch(/RU ·/);
  });

  it("owner-лот без города: флаг + ISO2 (город не обязателен в контракте)", () => {
    renderCard({ ...base, owner_type: "partner", country: "KZ" });
    expect(metaText()).toContain("🇰🇿 KZ");
  });

  it("страна в ленте рынка не мешает остальной метастроке (год · пробег · источник)", () => {
    renderCard({ ...base, country: "KR" });
    const meta = metaText();
    expect(meta).toContain("2022");
    expect(meta).toContain("42 000 км");
    expect(meta).toContain("🇰🇷 KR");
    expect(meta).toContain("KCar");
  });

  it("старый ответ без country — прежнее имя страны из словаря источников", () => {
    renderCard(base);
    expect(metaText()).toContain("Корея");
    expect(metaText()).not.toContain("🇰🇷 KR");
  });
});

describe("ListingCard: мини-карусель фото (ПК, задача владельца)", () => {  /** N фото с различимыми url. */
  const many = (n: number) => Array.from({ length: n }, (_, i) => `https://img/${i + 1}.jpg`);

  it("лимит 5 фото: слайдов ровно 5, на последнем «+N фото» (остальные — в PDP)", () => {
    renderCard({ ...base, images: many(8) });
    expect(document.querySelectorAll(".lcard__img")).toHaveLength(5);
    expect(document.querySelector(".lcarousel__more")?.textContent).toBe("+3 фото");
    // пилюля-счётчик показывает правду: всего фото 8, а не 5
    expect(document.querySelector(".lcard__count")?.textContent).toBe("8");
  });

  it("фото ≤5 — оверлея «+N» нет, карусель показывает все", () => {
    renderCard({ ...base, images: many(5) });
    expect(document.querySelectorAll(".lcard__img")).toHaveLength(5);
    expect(document.querySelector(".lcarousel__more")).toBeNull();
  });

  it("точки: по числу слайдов (до 5), первая активная; клик — scrollTo слайда и НЕ открывает карточку", () => {
    renderCard({ ...base, images: many(3) });
    const dots = document.querySelectorAll(".lcarousel__dot");
    expect(dots).toHaveLength(3);
    expect(dots[0].className).toContain("--active");
    // jsdom не считает layout: ширина и позиция слайда — через стабы
    const track = document.querySelector(".lcarousel") as HTMLDivElement;
    Object.defineProperty(track, "clientWidth", { configurable: true, value: 300 });
    const scrollTo = vi.fn();
    track.scrollTo = scrollTo as unknown as HTMLDivElement["scrollTo"];
    fireEvent.click(dots[2]);
    expect(scrollTo).toHaveBeenCalledWith({ left: 600, behavior: "smooth" });
    // клик по точке — управление каруселью, перехода в карточку нет
    expect(screen.queryByText("PDP-OPENED")).toBeNull();
    expect(document.querySelectorAll(".lcarousel__dot")[2].className).toContain("--active");
  });

  it("одно фото → точек-индикаторов нет", () => {
    renderCard({ ...base, images: many(1) });
    expect(document.querySelector(".lcarousel__dots")).toBeNull();
    expect(document.querySelector(".lcard__count")).toBeNull();
  });

  it("карусель: role=group + aria-roledescription + aria-label «Фото N из M», скролл переключает точку", () => {
    renderCard({ ...base, images: many(3) });
    const track = document.querySelector(".lcarousel") as HTMLDivElement;
    expect(track.getAttribute("role")).toBe("group");
    expect(track.getAttribute("aria-roledescription")).toBe("карусель");
    expect(track.getAttribute("aria-label")).toBe("Фото 1 из 3");
    // прокрутка к 2-му слайду (jsdom: scrollLeft/clientWidth — стабы)
    Object.defineProperty(track, "clientWidth", { configurable: true, value: 300 });
    track.scrollLeft = 300;
    fireEvent.scroll(track);
    expect(track.getAttribute("aria-label")).toBe("Фото 2 из 3");
    expect(document.querySelectorAll(".lcarousel__dot")[1].className).toContain("--active");
  });

  it("drag мышью со смещением >6px подавляет клик-переход в карточку", () => {
    renderCard({ ...base, images: many(3) });
    const track = document.querySelector(".lcarousel") as HTMLDivElement;
    fireEvent.pointerDown(track, {
      pointerId: 1,
      pointerType: "mouse",
      button: 0,
      clientX: 100,
      clientY: 10,
    });
    fireEvent.pointerMove(track, { pointerId: 1, pointerType: "mouse", clientX: 130, clientY: 12 });
    fireEvent.pointerUp(track, { pointerId: 1, pointerType: "mouse", clientX: 130 });
    fireEvent.click(document.querySelector(".lcard") as HTMLElement);
    expect(screen.queryByText("PDP-OPENED")).toBeNull();
  });

  it("дрожь ≤6px — ещё клик, а не жест: карточка открывается как обычно", () => {
    renderCard({ ...base, images: many(3) });
    const track = document.querySelector(".lcarousel") as HTMLDivElement;
    fireEvent.pointerDown(track, {
      pointerId: 1,
      pointerType: "mouse",
      button: 0,
      clientX: 100,
      clientY: 10,
    });
    fireEvent.pointerMove(track, { pointerId: 1, pointerType: "mouse", clientX: 103, clientY: 10 });
    fireEvent.pointerUp(track, { pointerId: 1, pointerType: "mouse", clientX: 103 });
    fireEvent.click(document.querySelector(".lcard") as HTMLElement);
    expect(screen.getByText("PDP-OPENED")).toBeTruthy();
  });
});

describe("ListingCard: бейдж владельца рынка (06 §4)", () => {
  const marketBadge = () => document.querySelector(".lcard__badge--market")?.textContent ?? null;

  it("owner_type=partner → «🏪 Салон»", () => {
    renderCard({ ...base, owner_type: "partner", has_market: true } as Listing & {
      has_market?: boolean;
    });
    expect(marketBadge()).toBe("🏪 Салон");
  });

  it("owner_type=user → «👤 Частник»", () => {
    renderCard({ ...base, owner_type: "user", has_market: true } as Listing & {
      has_market?: boolean;
    });
    expect(marketBadge()).toBe("👤 Частник");
  });

  it("owner_type нет, но has_market=true — фолбэк «🏪 Рынок» (переходный период)", () => {
    renderCard({ ...base, has_market: true } as Listing & { has_market?: boolean });
    expect(marketBadge()).toBe("🏪 Рынок");
  });

  it("внешний лот (encar/kcar): бейджа рынка нет вовсе", () => {
    renderCard(base);
    expect(marketBadge()).toBeNull();
  });
});

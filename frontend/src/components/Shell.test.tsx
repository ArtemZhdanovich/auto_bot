/** Тесты оболочки (Shell): таб-бар из 4 разделов по NAV-V2 §2 —
 *  Главная, Авто, Чаты (с бейджем непрочитанных), Профиль. */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { AppRoot } from "@telegram-apps/telegram-ui";
import { MemoryRouter, useLocation } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Shell } from "./Shell";
import { isGuest } from "../api/auth";
import { useChatUnread } from "../lib/chatStore";

vi.mock("../api/auth", () => ({ isGuest: vi.fn((): boolean => true) }));
vi.mock("../lib/chatStore", () => ({ useChatUnread: vi.fn((): number => 0) }));

const isGuestMock = vi.mocked(isGuest);
const unreadMock = vi.mocked(useChatUnread);

/** Зонд текущего пути — проверка навигации по клику на таб. */
function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{location.pathname}</div>;
}

afterEach(() => cleanup());
beforeEach(() => {
  isGuestMock.mockReturnValue(true); // гость — бейджа чатов нет
  unreadMock.mockReturnValue(0);
});

/** Рендер Shell внутри роутера; pathname — стартовый маршрут. */
const renderShell = (pathname = "/", desktop = false) =>
  render(
    <AppRoot appearance="light">
      <MemoryRouter initialEntries={[pathname]}>
        <LocationProbe />
        <Shell desktop={desktop} />
      </MemoryRouter>
    </AppRoot>,
  );

describe("Shell: мобильный таб-бар (NAV-V2)", () => {
  it("ровно 4 таба: Главная, Авто, Чаты, Профиль", () => {
    renderShell("/");
    const tabbar = document.querySelector(".app-tabbar") as HTMLElement;
    expect(tabbar).toBeTruthy();
    // самописная навигация — семантика nav
    expect(tabbar.closest("nav")?.getAttribute("aria-label")).toBe("Основная навигация");
    const tabs = tabbar.querySelectorAll("button");
    expect(tabs.length).toBe(4);
    for (const label of ["Главная", "Авто", "Чаты", "Профиль"]) {
      expect(within(tabbar).getByText(label)).toBeTruthy();
    }
  });

  it("старых табов (Лента/AI/Избранное/История) больше нет — они сегменты", () => {
    renderShell("/");
    const tabbar = document.querySelector(".app-tabbar") as HTMLElement;
    for (const label of ["Лента", "AI", "Избранное", "История"]) {
      expect(within(tabbar).queryByText(label)).toBeNull();
    }
  });

  it("активный таб помечен aria-current=page (и только он)", () => {
    renderShell("/");
    const marked = Array.from(document.querySelectorAll(".app-tabbar [aria-current]"));
    expect(marked.length).toBe(1);
    expect(marked[0].getAttribute("aria-current")).toBe("page");
    expect(marked[0].textContent).toContain("Главная");

    // на /auto метка переезжает на «Авто»
    cleanup();
    renderShell("/auto");
    const markedAuto = Array.from(document.querySelectorAll(".app-tabbar [aria-current]"));
    expect(markedAuto.length).toBe(1);
    expect(markedAuto[0].textContent).toContain("Авто");
  });

  it("комната чата /chats/:id держит активным таб «Чаты» (оверлей)", () => {
    renderShell("/chats/t-uuid");
    const marked = Array.from(document.querySelectorAll(".app-tabbar [aria-current]"));
    expect(marked.length).toBe(1);
    expect(marked[0].textContent).toContain("Чаты");
  });

  it("клик по табу переключает маршрут", async () => {
    renderShell("/");
    const tabbar = document.querySelector(".app-tabbar") as HTMLElement;
    fireEvent.click(within(tabbar).getByText("Авто"));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/auto"));
    const marked = document.querySelector('.app-tabbar [aria-current="page"]');
    expect(marked?.textContent).toContain("Авто");
  });

  it("бейдж непрочитанных — на табе «Чаты»; гостю бейджа нет", () => {
    isGuestMock.mockReturnValue(false);
    unreadMock.mockReturnValue(7);
    renderShell("/");
    const tabbar = document.querySelector(".app-tabbar") as HTMLElement;
    const badge = tabbar.querySelector(".app-tabbar__badge");
    expect(badge?.textContent).toBe("7");
    // aria-label подсказывает screen reader'у смысл цифры
    const chatsBtn = within(tabbar).getByText("Чаты").closest("button");
    expect(chatsBtn?.getAttribute("aria-label")).toBe("Чаты: 7 непрочитанных");

    // гость — бейджа нет (чаты закрыты)
    isGuestMock.mockReturnValue(true);
    cleanup();
    renderShell("/");
    expect(document.querySelector(".app-tabbar__badge")).toBeNull();
  });

  it("99+ непрочитанных — бейдж не растягивается", () => {
    isGuestMock.mockReturnValue(false);
    unreadMock.mockReturnValue(120);
    renderShell("/");
    expect(document.querySelector(".app-tabbar__badge")?.textContent).toBe("99+");
  });
});

describe("Shell: десктоп-sidebar (>= 768px)", () => {
  it("бренд + те же 4 пункта, активный — aria-current=page; конверта-бейджа нет", () => {
    renderShell("/", true);
    const sidebar = document.querySelector("nav.app-sidebar") as HTMLElement;
    expect(sidebar).toBeTruthy();
    expect(within(sidebar).getByText(/Auto Selection/)).toBeTruthy();
    const items = sidebar.querySelectorAll("button.sidebar-item");
    expect(items.length).toBe(4);
    for (const label of ["Главная", "Авто", "Чаты", "Профиль"]) {
      expect(within(sidebar).getByText(label)).toBeTruthy();
    }
    const marked = Array.from(sidebar.querySelectorAll('[aria-current="page"]'));
    expect(marked.length).toBe(1);
    expect(marked[0].textContent).toContain("Главная");
    // ChatBadge из строки бренда удалён (NAV-V2: бейдж — на табе/пункте Чаты)
    expect(sidebar.querySelector(".chat-badge")).toBeNull();
  });

  it("бейдж чатов — в пункте «Чаты» сайдбара", () => {
    isGuestMock.mockReturnValue(false);
    unreadMock.mockReturnValue(3);
    renderShell("/", true);
    const sidebar = document.querySelector("nav.app-sidebar") as HTMLElement;
    expect(sidebar.querySelector(".sidebar-item__badge")?.textContent).toBe("3");
  });

  it("клик по пункту sidebar переключает маршрут", async () => {
    renderShell("/", true);
    const sidebar = document.querySelector("nav.app-sidebar") as HTMLElement;
    fireEvent.click(within(sidebar).getByText("Чаты"));
    await waitFor(() => expect(screen.getByTestId("location").textContent).toBe("/chats"));
  });
});

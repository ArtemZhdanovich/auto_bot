/**
 * Интеграционные тесты партнёрского кабинета (jsdom + testing-library).
 * Сквозные сценарии редизайна 04-partner-redesign:
 * вход/запоминание ключа (X5), 401-баннер (X4), KPI денег на Обзоре (D1),
 * фильтры пула (P2), пре-валидация claim (P4), фидбек с подтверждением (M2),
 * копирование Telegram ID (M1), рынок с сохранёнными запросами (R1/R3),
 * биллинг с балансом (B1), ротация ключа (K2).
 *
 * HTTP мокается на уровне fetch: реальный api.ts (заголовок X-API-Key,
 * маппинг ошибок §7.9) участвует в тесте.
 */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PartnerApp } from "./PartnerApp";
import { clearPartnerKey, setPartnerKey } from "./api";

// ---------- Данные и моки ----------

const NOW = Date.parse("2026-10-24T12:00:00Z");
const MIN = 60_000;

const ME = {
  id: 1,
  name: "АвтоСалон",
  plan: "pro",
  daily_quota: 20,
  claimed_today: 12,
  quota_remaining: 8,
  balance: 240,
  cpl_cost: 12,
  key_prefix: "pi_ab12",
};

const POOL_LEADS = [
  {
    id: 101,
    source: "site",
    status: "new",
    comment: "Kia K5 срочно, рассматриваю до конца недели",
    qualification: { budget_usd: 25_000, timeframe: "2 недели", prepay: true },
    created_at: new Date(NOW - 5 * MIN).toISOString(), // свежий
  },
  {
    id: 102,
    source: "site",
    status: "new",
    comment: "Hyundai Sonata для семьи",
    qualification: { budget_usd: 8_000, timeframe: "месяц", prepay: false },
    created_at: new Date(NOW - 3 * 3_600_000).toISOString(), // 3 часа назад
  },
  {
    id: 103,
    source: "tg",
    status: "new",
    comment: "Что-нибудь экономичное",
    qualification: { budget_usd: 15_000, timeframe: null, prepay: null },
    created_at: new Date(NOW - 10 * MIN).toISOString(), // свежий
  },
];

const MINE_LEADS = [
  {
    id: 201,
    source: "site",
    status: "claimed",
    comment: "Ищу Kia K5",
    qualification: { budget_usd: 20_000, timeframe: "2 недели", prepay: true },
    created_at: new Date(NOW - 60 * MIN).toISOString(),
    partner_id: 1,
    partner_status: "claimed",
    claimed_at: new Date(NOW - 40 * MIN).toISOString(),
    user_id: 148812345,
  },
  {
    id: 202,
    source: "site",
    status: "claimed",
    comment: "Hyundai для такси",
    qualification: { budget_usd: 12_000, timeframe: null, prepay: null },
    created_at: new Date(NOW - 3 * 24 * 3_600_000).toISOString(),
    partner_id: 1,
    partner_status: "deal",
    claimed_at: new Date(NOW - 2 * 24 * 3_600_000).toISOString(),
    user_id: 222333444,
  },
];

const MARKET_RAW = {
  make: null,
  model: null,
  count: 1240,
  min_price: 5_000_000,
  max_price: 43_000_000,
  median_price: 24_300_000,
  deals: [
    {
      id: 55,
      make: "Kia",
      model: "K5",
      year: 2022,
      price: 21_300_000,
      currency: "KRW",
      discount_pct: 12.3,
      url: "https://encar.com/x",
    },
  ],
};

const BILLING_RAW = {
  plan: "pro",
  daily_quota: 20,
  claimed_today: 12,
  quota_remaining: 8,
  balance: 30,
  cpl_cost: 12,
  plans: [
    { key: "starter", title: "Starter", daily_quota: 5, cpl_cost: 15, description: "Для теста" },
    { key: "pro", title: "Pro", daily_quota: 20, cpl_cost: 12, description: null },
  ],
};

function okRes(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}
function failRes(status: number, detail: string): Response {
  return { ok: false, status, json: async () => ({ detail }) } as Response;
}

type RouteFn = (url: string, init?: RequestInit) => Response | undefined;

/** Стандартная маршрутизация бэкенда партнёра (всё «ок»). */
function defaultRoutes(over: Partial<Record<"me" | "pool" | "mine" | "market" | "billing", unknown>> = {}): RouteFn {
  return (url) => {
    if (url.endsWith("/api/partner/me")) return okRes(over.me ?? ME);
    if (url.includes("/leads/batch")) return okRes({ claimed: [] });
    if (url.startsWith("/api/partner/leads?mine=true")) return okRes(over.mine ?? MINE_LEADS);
    if (url.startsWith("/api/partner/leads")) return okRes(over.pool ?? POOL_LEADS);
    if (url.includes("/api/partner/market")) return okRes(over.market ?? MARKET_RAW);
    if (url.endsWith("/api/partner/billing")) return okRes(over.billing ?? BILLING_RAW);
    return okRes({});
  };
}

function mockFetch(route: RouteFn) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    return route(String(input), init) ?? okRes({});
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** Тач/мобайл-окружение: карточный режим таблиц (P1/M5). */
function stubMatchMedia(matches: boolean) {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    configurable: true,
    value: (q: string) => ({
      matches: q.includes("max-width") ? matches : false,
      media: q,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
    }),
  });
}

const clipboardWrite = vi.fn(async () => undefined);

function renderPartner(path = "/partner") {
  window.history.pushState({}, "", path);
  return render(<PartnerApp />);
}

beforeEach(() => {
  stubMatchMedia(false); // десктоп: таблицы
  sessionStorage.clear();
  localStorage.clear();
  clearPartnerKey();
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: clipboardWrite },
  });
  clipboardWrite.mockClear();
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW });
  vi.setSystemTime(NOW);
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
  vi.unstubAllGlobals();
  sessionStorage.clear();
  localStorage.clear();
});

// ---------- Вход и оболочка ----------

describe("партнёрский кабинет: вход и навигация", () => {
  it("вход: поле с маской, «Запомнить на этом устройстве», ключ в localStorage (X5)", async () => {
    mockFetch(defaultRoutes());
    renderPartner();

    expect(screen.getByText(/Войдите с API-ключом, выданным менеджером/)).toBeTruthy();
    const field = screen.getByLabelText("API-ключ партнёра") as HTMLInputElement;
    expect(field.type).toBe("password"); // ключ не светится на экране (X5)
    expect(screen.getByLabelText("Запомнить на этом устройстве")).toBeTruthy();

    fireEvent.change(field, { target: { value: "pi_secret1" } });
    fireEvent.click(screen.getByRole("button", { name: "Войти" }));

    expect(await screen.findByRole("heading", { name: "Обзор" })).toBeTruthy();
    // чекбокс включён по умолчанию — ключ пережил бы закрытие браузера
    expect(localStorage.getItem("autobot.partnerKey")).toBe("pi_secret1");

    // навигация: порядок разделов, «Ключ API» и «Выйти» — в сайдбаре, не в табах (N1/N2)
    expect(screen.getAllByText("Пул лидов").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Мои лиды").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Рынок").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Биллинг").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Ключ API").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Выйти" })).toBeTruthy();
    // шапка: имя счёта, план, баланс (§3.2)
    expect(screen.getByText("АвтоСалон")).toBeTruthy();
    expect(screen.getByText("Pro")).toBeTruthy();
    expect(screen.getAllByText("$240").length).toBeGreaterThan(0);
  });

  it("401 на старте: ключ отозван — экран входа с баннером-причиной (X4)", async () => {
    setPartnerKey("pi_dead", false);
    mockFetch(() => failRes(401, "invalid key"));
    renderPartner();

    expect(await screen.findByText("Ключ отозван или сменён — введите новый.")).toBeTruthy();
    expect(screen.getByRole("heading", { name: "Кабинет партнёра" })).toBeTruthy();
  });

  it("сбой /me (5xx): ошибка с «Повторить» вместо вечного скелетона (X3)", async () => {
    mockFetch((url) =>
      url.endsWith("/api/partner/me") ? failRes(502, "boom") : defaultRoutes()(url),
    );
    setPartnerKey("pi_ok", false);
    renderPartner();

    expect(
      await screen.findByText(
        "Сервис временно недоступен. Повторите через минуту — ваши лиды и баланс не теряются.",
      ),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: "Повторить" })).toBeTruthy();
  });

  it("выход возвращает на вход без перезагрузки приложения (N3)", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner();
    await screen.findByRole("heading", { name: "Обзор" });

    fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
    // SPA жив: экран входа в том же дереве, ключ снят
    expect(await screen.findByRole("button", { name: "Войти" })).toBeTruthy();
    expect(sessionStorage.getItem("autobot.partnerKey")).toBeNull();
    expect(localStorage.getItem("autobot.partnerKey")).toBeNull();
  });

  it("гонка выхода: поздний ответ /me не открывает кабинет без ключа", async () => {
    // claim обновляет /me (onChanged): ответ может прийти уже после «Выйти» —
    // кабинет не должен воскреснуть, а poll — показать тост на экране входа.
    let meCalls = 0;
    let lateMe!: () => void;
    mockFetch((url) => {
      const u = String(url);
      if (u.endsWith("/api/partner/me")) {
        meCalls += 1;
        if (meCalls === 1) return okRes(ME);
        return new Promise<Response>((r) => {
          lateMe = () => r(okRes(ME));
        });
      }
      return defaultRoutes()(u) ?? okRes({});
    });
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/pool");
    expect(await screen.findAllByRole("button", { name: "Забрать · $12" })).toBeTruthy();

    // claim уходит, /me (2-й) зависает в полёте → выходим
    fireEvent.click(screen.getAllByRole("button", { name: "Забрать · $12" })[0]);
    await screen.findByText(/Лид забран/);
    fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
    expect(await screen.findByRole("button", { name: "Войти" })).toBeTruthy();

    // поздний успешный /me приходит уже без ключа — кабинет не открывается
    await act(async () => {
      lateMe();
    }); // сброс микротасков цепочки papi→setState + рендер React
    // остаёмся на входе; баннера «ключ отозван» нет — выход был добровольным
    expect(screen.getByRole("button", { name: "Войти" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Выйти" })).toBeNull();
    expect(screen.queryByText("Ключ отозван или сменён — введите новый.")).toBeNull();
  });
});

// ---------- Навигация v4 (NAV-V2 §3): таб-бар 5 + шторка «Ещё» ----------

describe("навигация v4: моб-таб-бар 5 + шторка «Ещё»", () => {
  it("таб-бар из 5 пунктов: Обзор, Пул, Мои, Чаты, Ещё (NAV-V2 §3)", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner();

    await screen.findByRole("heading", { name: "Обзор" });
    const tabs = Array.from(document.querySelectorAll(".pa-tabbar .pa-tab"));
    expect(tabs).toHaveLength(5);
    expect(tabs.map((t) => t.textContent)).toEqual(["Обзор", "Пул", "Мои", "Чаты", "Ещё"]);
    // разделов группы «Ещё» в самом таб-баре нет — только в шторке
    expect(document.querySelector(".pa-tabbar")?.textContent).not.toContain("Рынок");
  });

  it("«Ещё» открывает шторку с 5 разделами; закрытие по Esc и scrim", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner();

    await screen.findByRole("heading", { name: "Обзор" });
    fireEvent.click(screen.getByRole("button", { name: "Ещё" }));

    // шторка: Рынок, Витрина, Объявления, Чеки и история, Биллинг (NAV-V2 §3)
    const sheet = document.querySelector(".pa-sheet");
    expect(sheet).toBeTruthy();
    const labels = Array.from(sheet!.querySelectorAll(".pa-more__item")).map((a) => a.textContent);
    expect(labels).toEqual(["Рынок", "Витрина", "Объявления", "Чеки и история", "Биллинг"]);

    // закрытие по Esc
    fireEvent.keyDown(document, { key: "Escape" });
    expect(document.querySelector(".pa-sheet")).toBeNull();

    // закрытие по scrim (клик по подложке мимо панели)
    fireEvent.click(screen.getByRole("button", { name: "Ещё" }));
    fireEvent.click(document.querySelector(".pa-sheet-wrap")!);
    expect(document.querySelector(".pa-sheet")).toBeNull();
  });

  it("в шторке у «Биллинга» точка низкого баланса (lowBalance, X1)", async () => {
    mockFetch(defaultRoutes({ me: { ...ME, balance: 24 } })); // 24 < 3×12 → warn
    setPartnerKey("pi_ok", false);
    renderPartner();

    await screen.findByRole("heading", { name: "Обзор" });
    fireEvent.click(screen.getByRole("button", { name: "Ещё" }));
    const billing = Array.from(document.querySelectorAll(".pa-more__item")).find((a) =>
      a.textContent?.includes("Биллинг"),
    );
    expect(billing?.querySelector(".pa-navbadge--dot")).toBeTruthy();
  });

  it("переход из шторки: раздел открыт, таб «Ещё» активен с точкой, текущий — с галочкой", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner();

    await screen.findByRole("heading", { name: "Обзор" });
    fireEvent.click(screen.getByRole("button", { name: "Ещё" }));
    // в шторке — не сайдбарный «Рынок»: скоуп по панели шторки
    fireEvent.click(within(document.querySelector(".pa-sheet")!).getByRole("link", { name: "Рынок" }));

    await waitFor(() => expect(window.location.pathname).toBe("/partner/market"));
    expect(await screen.findByText("Весь рынок · 1 240 объявл.")).toBeTruthy();
    // шторка закрылась выбором
    expect(document.querySelector(".pa-sheet")).toBeNull();

    // активный путь из группы «Ещё» подсвечивает таб «Ещё» + точка (NAV-V2 §3)
    const moreTab = screen.getByRole("button", { name: "Ещё" });
    expect(moreTab.className).toContain("pa-tab--active");
    expect(moreTab.querySelector(".pa-tab__dot")).toBeTruthy();

    // повторное открытие: у текущего раздела галочка
    fireEvent.click(moreTab);
    const current = document.querySelector(".pa-more__item--active");
    expect(current?.textContent).toContain("Рынок");
    expect(current?.querySelector(".pa-more__check")).toBeTruthy();
  });

  it("десктоп-сайдбар: все 9 разделов списком, без сворачивания (NAV-V2 §3)", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner();

    await screen.findByRole("heading", { name: "Обзор" });
    const sidebarItems = Array.from(document.querySelectorAll(".pa-sidebar__nav .pa-navitem"));
    expect(sidebarItems).toHaveLength(9);
    expect(sidebarItems.map((i) => i.textContent)).toEqual([
      "Обзор",
      "Пул лидов",
      "Мои лиды",
      "Чаты",
      "Рынок",
      "Витрина",
      "Объявления",
      "Чеки и история",
      "Биллинг",
    ]);
  });
});

// ---------- Обзор (§4.1) ----------

describe("Обзор: деньги и быстрые действия (D1/D3)", () => {
  it("KPI: баланс «хватит на N», квота, batch «Взять до 8»", async () => {
    const fetchMock = mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner();

    // плитка баланса с ценой лида (D1): $240 · хватит на 20 лидов
    expect(await screen.findByText("хватит на 20 лидов")).toBeTruthy();
    expect(screen.getByText("Квота на сегодня")).toBeTruthy();
    expect(screen.getByText("12 из 20")).toBeTruthy();
    // N = min(квота 8, floor(240/12)=20, 10) = 8
    expect(screen.getByRole("button", { name: "Взять до 8 лидов" })).toBeTruthy();

    // batch с Обзора (§3.3: 1 тап до «забрать»)
    fetchMock.mockImplementation(async (input: RequestInfo | URL) =>
      String(input).includes("/leads/batch")
        ? okRes({ claimed: [{ lead_id: 7, partner_id: 1, partner_status: "claimed", user_id: 1, qualification: null, claimed_at: null }] })
        : (defaultRoutes()(String(input)) ?? okRes({})),
    );
    fireEvent.click(screen.getByRole("button", { name: "Взять до 8 лидов" }));
    expect(await screen.findByText(/Забрано лидов: 1/)).toBeTruthy();
    // тост с действием «Открыть» (P7)
    expect(screen.getByRole("button", { name: "Открыть" })).toBeTruthy();
  });

  it("низкий баланс: жёлтый баннер с заготовкой менеджеру (§4.1.2)", async () => {
    mockFetch(
      defaultRoutes({
        me: { ...ME, balance: 24, quota_remaining: 8 }, // хватит на 2 < 3 → warn
      }),
    );
    setPartnerKey("pi_ok", false);
    renderPartner();

    expect(await screen.findByText(/Баланса хватит на 2 лида/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Написать менеджеру" }));
    await waitFor(() =>
      expect(clipboardWrite).toHaveBeenCalledWith(expect.stringContaining("пополнить баланс")),
    );
    expect(await screen.findByText(/Заготовка скопирована/)).toBeTruthy();
  });
});

// ---------- Пул лидов (§4.2) ----------

describe("Пул лидов: фильтры, claim, пре-валидация (P1/P2/P4)", () => {
  it("клиентские фильтры: поиск, чипы бюджета, «Только новые» (P2)", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/pool");

    expect(await screen.findByText("#101")).toBeTruthy();
    expect(screen.getByText("#102")).toBeTruthy();
    expect(screen.getByText("#103")).toBeTruthy();

    // поиск по подстроке комментария
    fireEvent.change(screen.getByLabelText("Поиск по лидам"), { target: { value: "kia" } });
    expect(screen.getByText("#101")).toBeTruthy();
    expect(screen.queryByText("#102")).toBeNull();

    // сброс и чип бюджета «до $10 тыс.» — остаётся только #102 ($8 000)
    fireEvent.change(screen.getByLabelText("Поиск по лидам"), { target: { value: "" } });
    fireEvent.click(screen.getByRole("button", { name: "до $10 тыс." }));
    expect(screen.queryByText("#101")).toBeNull();
    expect(screen.getByText("#102")).toBeTruthy();

    // «Только новые» — свежие #101 и #103 (бюджетный чип снимаем)
    fireEvent.click(screen.getByRole("button", { name: "до $10 тыс." }));
    fireEvent.click(screen.getByRole("button", { name: "Только новые" }));
    expect(screen.getByText("#101")).toBeTruthy();
    expect(screen.queryByText("#102")).toBeNull();

    // «Новый» бейдж и относительное время (P6)
    expect(screen.getAllByText("Новый").length).toBeGreaterThan(0);
    expect(screen.getAllByText("5 мин назад").length).toBeGreaterThan(0);
  });

  it("claim: цена на кнопке, успех — fade-out и тост с «Открыть» (P4/P7)", async () => {
    const fetchMock = mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/pool");

    const btn = (await screen.findAllByRole("button", { name: "Забрать · $12" }))[0];
    fireEvent.click(btn);

    expect(await screen.findByText("✅ Лид забран — контакты в «Моих лидах»")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/leads/101/claim",
      expect.objectContaining({ method: "POST" }),
    );
    // action-кнопка ведёт в «Мои лиды» (P7)
    fireEvent.click(screen.getByRole("button", { name: "Открыть" }));
    await waitFor(() =>
      expect(window.location.pathname).toBe("/partner/leads"),
    );
  });

  it("квота исчерпана: кнопка «Забрать» дизейблится с пояснением (P4)", async () => {
    stubMatchMedia(true); // мобильные карточки — пояснение видно текстом
    mockFetch(defaultRoutes({ me: { ...ME, quota_remaining: 0, claimed_today: 20 } }));
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/pool");

    const btn = (await screen.findAllByRole("button", { name: "Забрать · $12" }))[0];
    expect((btn as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getAllByText("Квота на сегодня исчерпана — обновится завтра").length).toBeGreaterThan(0);
  });

  it("пустой пул: состояние по копирайту §7.3", async () => {
    mockFetch(defaultRoutes({ pool: [] }));
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/pool");

    expect(await screen.findByText("Свободных лидов сейчас нет")).toBeTruthy();
    expect(screen.getByText(/бейдж на вкладке «Пул» вас предупредит/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Обновить" })).toBeTruthy();
  });
});

// ---------- Мои лиды (§4.3) ----------

describe("Мои лиды: контакты и фидбек (M1/M2/M4)", () => {
  it("Telegram ID — deep-link и копирование одной кнопкой (M1)", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/leads");

    await screen.findByText("Ищу Kia K5");
    const link = document.querySelector('a[href="tg://user?id=148812345"]');
    expect(link).toBeTruthy();
    expect(screen.getByText("Найдите клиента по ID в поиске Telegram")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Копировать ID" }));
    await waitFor(() => expect(clipboardWrite).toHaveBeenCalledWith("148812345"));
    expect(await screen.findByText(/ID скопирован/)).toBeTruthy();
  });

  it("фильтры-очередь «Ждут отметки N» (M4) и фидбек: «Сделка» в 1 клик", async () => {
    const fetchMock = mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/leads");

    // счётчик «ждут отметки» прямо в чипе (M4)
    const chip = await screen.findByRole("button", { name: /Ждут отметки/ });
    expect(chip.textContent).toContain("1");

    fireEvent.click(screen.getAllByRole("button", { name: "Сделка" })[0]);
    await screen.findByText("✅ Отметка сохранена: сделка");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/leads/201/feedback",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ status: "deal" }),
      }),
    );
  });

  it("«Возврат» — с подтверждающим шагом (M2): «Отметка влияет на доступ к пулу»", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/leads");

    await screen.findByText("Ищу Kia K5");
    fireEvent.click(screen.getByRole("button", { name: "Возврат" }));
    expect(screen.getByText('Отметить «Возврат»?')).toBeTruthy();
    expect(screen.getByText("Отметка влияет на доступ к пулу.")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Отметить" }));
    expect(await screen.findByText("✅ Отметка сохранена: возврат")).toBeTruthy();

    // статусная пилюля обновилась: в «Закрытых» появился «Возврат» (M3)
    fireEvent.click(screen.getByRole("button", { name: /Закрытые/ }));
    await waitFor(() => expect(document.querySelector(".pa-pill--returned")).toBeTruthy());
  });
});

// ---------- Рынок (§4.4) ----------

describe("Рынок: сводка, лоты, сохранённые запросы (R1/R3/R4)", () => {
  it("сводка среза + двойное действие лота: «Карточка» и «Источник ↗» (R3)", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/market");

    expect(await screen.findByText("Весь рынок · 1 240 объявл.")).toBeTruthy();
    // компактный формат миллионов (R4) + медиана — главная
    expect(screen.getByText("24,3 млн KRW")).toBeTruthy();
    // бейдж выгоды −12% (deal-solid)
    expect(screen.getByText("−12%")).toBeTruthy();
    // внутренняя карточка мини-аппа и внешний источник — два действия (R3)
    expect(document.querySelector('a[href="/listing/55"]')?.textContent).toContain("Карточка");
    expect(document.querySelector('a[href="https://encar.com/x"]')?.textContent).toContain("Источник");
  });

  it("запрос Kia K5 сохраняется чипом в localStorage (R1)", async () => {
    const fetchMock = mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/market");

    await screen.findByText("Весь рынок · 1 240 объявл.");
    fireEvent.change(screen.getByLabelText("Марка"), { target: { value: "Kia" } });
    fireEvent.change(screen.getByLabelText("Модель"), { target: { value: "K5" } });
    fireEvent.click(screen.getByRole("button", { name: "Показать" }));

    expect(await screen.findByText("Kia K5 · 1 240 объявл.")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/partner/market?make=Kia&model=K5"),
      expect.anything(),
    );
    // чип сохранённого запроса + отдельная кнопка удаления (§6.2 SavedQueryChip)
    expect(screen.getByRole("button", { name: "Kia K5" })).toBeTruthy();
    expect(JSON.parse(localStorage.getItem("autobot.partner.marketQueries") ?? "[]")).toEqual([
      { make: "Kia", model: "K5" },
    ]);
    fireEvent.click(screen.getByRole("button", { name: 'Удалить запрос «Kia K5»' }));
    expect(JSON.parse(localStorage.getItem("autobot.partner.marketQueries") ?? "[]")).toEqual([]);
  });
});

// ---------- Биллинг (§4.5) ----------

describe("Биллинг: баланс, CPL, тарифы (B1/B4)", () => {
  it("плитки денег и предупреждение «хватит на 2 лида» (B1)", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/billing");

    expect(await screen.findByText("Хватит на")).toBeTruthy();
    expect(screen.getByText("2 лида")).toBeTruthy(); // $30 / $12
    expect(screen.getByText(/Баланса хватит на 2 лида/)).toBeTruthy();
    // модель оплаты и обновление квоты
    expect(screen.getByText(/Обновится завтра в 00:00/)).toBeTruthy();
  });

  it("каталог тарифов: колонка «Цена лида», описание и «текущий» (B4)", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/billing");

    expect(await screen.findByRole("columnheader", { name: "Цена лида" })).toBeTruthy();
    expect(screen.getByText("Тарифы")).toBeTruthy();
    expect(screen.getByText("Для теста")).toBeTruthy(); // description тарифа
    expect(screen.getByText("текущий")).toBeTruthy();
    // транзакций в API нет — секции не рисуем (§4.5.3, B2)
    expect(screen.queryByText("Транзакции")).toBeNull();
  });
});

// ---------- Ключ API (§4.6) ----------

describe("Ключ API: ротация с подтверждением и копирование примера (K1/K2)", () => {
  it("ротация: подтверждение → заготовка менеджеру скопирована", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/key");

    expect(await screen.findByRole("heading", { name: "Ключ API" })).toBeTruthy();
    expect(screen.getByText("pi_ab12…")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Запросить ротацию" }));
    expect(screen.getByText("Запросить новый ключ?")).toBeTruthy();
    expect(
      screen.getByText(/Текущий перестанет работать сразу/),
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Запросить у менеджера" }));
    await waitFor(() =>
      expect(clipboardWrite).toHaveBeenCalledWith(
        expect.stringContaining("ротировать ключ партнёра «АвтоСалон»"),
      ),
    );
    expect(await screen.findByText(/Заготовка скопирована/)).toBeTruthy();
  });

  it("копируется пример запроса, а не бесполезный префикс (K1)", async () => {
    mockFetch(defaultRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/key");

    await screen.findByText("Документация API");
    fireEvent.click(screen.getByRole("button", { name: "Скопировать пример" }));
    await waitFor(() =>
      expect(clipboardWrite).toHaveBeenCalledWith(expect.stringContaining("/api/partner/me")),
    );
    expect(await screen.findByText(/Пример запроса скопирован/)).toBeTruthy();
  });
});

// ---------- Ошибки API → русские строки (X2, §7.9) ----------

describe("маппинг ошибок claim (X2)", () => {
  it("402 → «Недостаточно средств» с кнопкой «К биллингу»", async () => {
    mockFetch((url) => {
      if (url.includes("/claim")) return failRes(402, "Insufficient balance: top up to claim leads");
      return defaultRoutes()(url);
    });
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/pool");

    fireEvent.click((await screen.findAllByRole("button", { name: "Забрать · $12" }))[0]);
    expect(
      await screen.findByText("Недостаточно средств на балансе. Пополните счёт у менеджера, чтобы забирать лиды."),
    ).toBeTruthy();
    const toBilling = screen.getByRole("button", { name: "К биллингу" });
    fireEvent.click(toBilling);
    await waitFor(() => expect(window.location.pathname).toBe("/partner/billing"));
  });

  it("429 квота → «Дневная квота исчерпана» с кнопкой «К обзору»", async () => {
    mockFetch((url) => {
      if (url.includes("/claim")) return failRes(429, "Daily quota exceeded");
      return defaultRoutes()(url);
    });
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/pool");

    fireEvent.click((await screen.findAllByRole("button", { name: "Забрать · $12" }))[0]);
    expect(await screen.findByText("Дневная квота исчерпана. Она обновится завтра.")).toBeTruthy();
    expect(screen.getByRole("button", { name: "К обзору" })).toBeTruthy();
  });

  it("409 → «Лид уже забрал другой партнёр» с «Обновить»", async () => {
    const fetchMock = mockFetch((url) => {
      if (url.includes("/claim")) return failRes(409, "Lead claim failed: taken");
      return defaultRoutes()(url);
    });
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/pool");

    fireEvent.click((await screen.findAllByRole("button", { name: "Забрать · $12" }))[0]);
    expect(
      await screen.findByText("Лид уже забрал другой партнёр. Обновите пул — там есть другие."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Обновить" }));
    await waitFor(() => {
      const poolLoads = fetchMock.mock.calls.filter(([u]) =>
        String(u).startsWith("/api/partner/leads?limit"),
      );
      expect(poolLoads.length).toBeGreaterThanOrEqual(2); // пул перечитан
    });
  });
});

// ---------- Объявления (06-market-user-listings.md §6): пакеты + лоты ----------

const MKT_PACKAGES = [
  { size: 10, price_rub: 500 },
  { size: 50, price_rub: 1800 },
  { size: 200, price_rub: 5000 },
];

/** Контракт MyLotOut бэка (listing_id/images/placement_*). */
const MKT_LOTS = [
  {
    listing_id: 71,
    placement_status: "slot",
    is_active: true,
    placement_free_until: null,
    placement_paid_until: null,
    make: "Kia",
    model: "K5",
    year: 2022,
    price: 20_000_000,
    currency: "KRW",
    mileage_km: 30_000,
    images: ["https://img/p1.jpg"],
    url: null,
    owner_description: null,
  },
];

/** Роуты контура market/packages + market/lots поверх стандартных. */
function adsRoutes(
  over: {
    packages?: unknown;
    pkgBuy?: Response;
    lots?: unknown[];
    placeFail?: number;
  } = {},
): RouteFn {
  return (url, init) => {
    if (url.endsWith("/api/partner/market/packages")) {
      if (init?.method === "POST") {
        if (over.pkgBuy) return over.pkgBuy;
        return okRes({ size: 10, price_rub: 500, slots_free: 10 });
      }
      return okRes(over.packages ?? MKT_PACKAGES);
    }
    if (url.endsWith("/api/partner/market/lots") && init?.method === "POST") {
      if (over.placeFail) return failRes(over.placeFail, "placement_slots_exhausted");
      return okRes({ listing_id: 72, placement_status: "slot", is_active: true, needs_payment: false });
    }
    if (url.endsWith("/api/partner/market/lots")) return okRes(over.lots ?? MKT_LOTS);
    if (/\/api\/partner\/market\/lots\/\d+$/.test(url) && init?.method === "DELETE") {
      return okRes(undefined);
    }
    return defaultRoutes()(url, init);
  };
}

describe("Объявления: пакеты размещений (06 §6)", () => {
  it("карточки size/₽; «Купить» с подтверждением → тост «Пакет активирован: N мест» + слоты", async () => {
    const fetchMock = mockFetch(adsRoutes());
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/ads");

    await screen.findByText("Пакеты размещений");
    expect(screen.getByText("500 ₽")).toBeTruthy();
    expect(screen.getByText("1 800 ₽")).toBeTruthy();
    // до покупки свободные места неизвестны
    expect(screen.getByText(/Свободно мест/).textContent).toContain("—");

    fireEvent.click(screen.getAllByRole("button", { name: "Купить" })[0]);
    expect(window.confirm).toHaveBeenCalledWith("Купить пакет на 10 размещений за 500 ₽?");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/market/packages",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ size: 10 }) }),
    );
    expect(await screen.findByText("Пакет активирован: 10 мест")).toBeTruthy();
    // счётчик мест обновился ответом покупки
    expect(screen.getByText(/Свободно мест/).textContent).toContain("10");
    confirmSpy.mockRestore();
  });

  it("402 при покупке → тост «Недостаточно средств», пакет не активируется", async () => {
    mockFetch(adsRoutes({ pkgBuy: failRes(402, "insufficient_balance") }));
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/ads");

    fireEvent.click((await screen.findAllByRole("button", { name: "Купить" }))[0]);
    expect(
      await screen.findByText("Недостаточно средств на балансе. Пополните счёт у менеджера."),
    ).toBeTruthy();
    expect(screen.queryByText(/Пакет активирован/)).toBeNull();
    confirmSpy.mockRestore();
  });

  it("отказ от подтверждения — POST покупки не уходит", async () => {
    const fetchMock = mockFetch(adsRoutes());
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/ads");

    fireEvent.click((await screen.findAllByRole("button", { name: "Купить" }))[0]);
    const buys = fetchMock.mock.calls.filter(
      ([u, i]) => String(u).endsWith("/api/partner/market/packages") && i?.method === "POST",
    );
    expect(buys).toHaveLength(0);
    confirmSpy.mockRestore();
  });
});

describe("Объявления: размещение лота (06 §6)", () => {
  /** Заполнить форму минимально валидными полями. */
  const fillForm = async () => {
    fireEvent.change(await screen.findByLabelText("Марка"), { target: { value: "Kia" } });
    fireEvent.change(screen.getByLabelText("Модель"), { target: { value: "K5" } });
    fireEvent.change(screen.getByLabelText("Год выпуска"), { target: { value: "2022" } });
    fireEvent.change(screen.getByLabelText("Цена"), { target: { value: "20000000" } });
  };

  it("валидация: пустая форма — ошибки, POST не уходит", async () => {
    const fetchMock = mockFetch(adsRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/ads");

    fireEvent.click(await screen.findByRole("button", { name: "Разместить" }));
    expect(await screen.findByText("Укажите марку")).toBeTruthy();
    expect(screen.getByText("Укажите цену")).toBeTruthy();
    const places = fetchMock.mock.calls.filter(
      ([u, i]) => String(u).endsWith("/api/partner/market/lots") && i?.method === "POST",
    );
    expect(places).toHaveLength(0);
  });

  it("201: тело как user-размещение → тост «Лот размещён», список перечитан", async () => {
    const fetchMock = mockFetch(adsRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/ads");

    await fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Разместить" }));
    await screen.findByText("✅ Лот размещён — он уже в ленте рынка");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/market/lots",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          make: "Kia",
          model: "K5",
          year: 2022,
          price: 20_000_000,
          currency: "KRW",
        }),
      }),
    );
    // список размещённых перечитан после публикации
    const listLoads = fetchMock.mock.calls.filter(
      ([u, i]) => String(u).endsWith("/api/partner/market/lots") && !i?.method,
    );
    expect(listLoads.length).toBeGreaterThanOrEqual(2);
  });

  it("409 placement_slots_exhausted → тост про пакет, лот не размещён", async () => {
    mockFetch(adsRoutes({ placeFail: 409 }));
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/ads");

    await fillForm();
    fireEvent.click(screen.getByRole("button", { name: "Разместить" }));
    expect(await screen.findByText("Свободных мест нет — купите пакет размещений")).toBeTruthy();
    expect(screen.queryByText(/Лот размещён/)).toBeNull();
  });
});

describe("Объявления: список размещённых (06 §6)", () => {
  it("статус «Размещено» (slot) + «Снять» → DELETE без возврата слота", async () => {
    const fetchMock = mockFetch(adsRoutes());
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/ads");

    expect(await screen.findByText("Kia K5, 2022")).toBeTruthy();
    expect(screen.getByText("Размещено")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "Снять" }));
    expect(window.confirm).toHaveBeenCalledWith(
      "Снять объявление? Место пакета не возвращается.",
    );
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/market/lots/71",
      expect.objectContaining({ method: "DELETE" }),
    );
    expect(await screen.findByText("Объявление снято")).toBeTruthy();
    confirmSpy.mockRestore();
  });

  it("пустой список — EmptyBlock с подсказкой", async () => {
    mockFetch(adsRoutes({ lots: [] }));
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/ads");
    expect(await screen.findByText("Размещений пока нет")).toBeTruthy();
  });
});

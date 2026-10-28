/**
 * Интеграционные тесты F2 «Чеки и история» (07-sell-chats-partner-
 * inspection.md §3): список своих лотов с индикатором чека, конструктор
 * чека (клик зоны → тип → пин, чек-лист, шапка) и история машины.
 * HTTP мокается на уровне fetch — реальный partner/api.ts (X-API-Key,
 * 404→«чека нет») участвует в тесте, маппинг papi как в partner.test.tsx.
 */
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { PartnerApp } from "./PartnerApp";
import { clearPartnerKey, setPartnerKey } from "./api";

// ---------- Данные и моки ----------

const NOW = Date.parse("2026-10-25T12:00:00Z");

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

/** Мои лоты (MyLotOut): 71 — с чеком, 72 — без. */
const LOTS = [
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
    images: [],
    url: null,
    owner_description: null,
  },
  {
    listing_id: 72,
    placement_status: "slot",
    is_active: true,
    placement_free_until: null,
    placement_paid_until: null,
    make: "Hyundai",
    model: "Sonata",
    year: 2021,
    price: 17_000_000,
    currency: "KRW",
    mileage_km: 41_000,
    images: [],
    url: null,
    owner_description: null,
  },
];

/** Существующий чек лота 71 (InspectionReportOut). */
const REPORT_71 = {
  source: "partner",
  inspector: "АвтоТех, Сеул",
  inspected_at: "2026-10-20T09:00:00Z",
  grade: "good",
  summary: "Состояние хорошее, есть мелкие недочёты",
  marks: [{ zone: "hood", type: "scratch", note: "полировка", pos: [120, 47] }],
  items: [{ key: "engine", status: "defect", note: "потёрт ремень" }],
  images: ["https://img.example/f1.jpg"],
};

function okRes(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}
function failRes(status: number, detail: string): Response {
  return { ok: false, status, json: async () => ({ detail }) } as Response;
}

type RouteFn = (url: string, init?: RequestInit) => Response | undefined;

/**
 * Маршруты контура inspections поверх стандартных: GET inspection лота 71
 * отдаёт чек, 72 — 404 («чека нет»); PUT-эндпоинты отвечают успехом.
 */
function inspRoutes(
  over: {
    report71?: unknown;
    lots?: unknown[];
    history?: { events?: unknown[] };
    putInspectionFail?: number;
  } = {},
): RouteFn {
  return (url, init) => {
    if (url.endsWith("/api/partner/me")) return okRes(ME);
    if (url.includes("/leads")) return okRes([]);
    if (url.endsWith("/api/partner/chats")) return okRes([]);
    if (url.endsWith("/api/partner/market/lots")) return okRes(over.lots ?? LOTS);
    if (url.endsWith("/api/partner/market/packages")) return okRes([]);
    if (url.includes("/api/partner/market")) return okRes({ items: [] });
    if (/\/api\/partner\/listings\/71\/inspection$/.test(url)) {
      return okRes(over.report71 ?? REPORT_71);
    }
    if (/\/api\/partner\/listings\/72\/inspection$/.test(url)) {
      if (init?.method === "PUT") {
        if (over.putInspectionFail) return failRes(over.putInspectionFail, "boom");
        return okRes(REPORT_71);
      }
      return failRes(404, "inspection_not_found");
    }
    if (/\/api\/partner\/listings\/\d+\/inspection$/.test(url) && init?.method === "PUT") {
      return okRes(REPORT_71);
    }
    if (/\/api\/partner\/listings\/(\d+)\/history$/.test(url) && init?.method === "PUT") {
      return okRes({ updated: true });
    }
    // публичная карточка: редактор истории читает owner_history
    if (/\/api\/listings\/71$/.test(url)) {
      return okRes({
        owner_history: [{ date: "2023-05-01", title: "Пригон из Кореи", note: null }],
      });
    }
    if (/\/api\/listings\/\d+$/.test(url)) return okRes(over.history ?? {});
    return okRes(ME);
  };
}

function mockFetch(route: RouteFn) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    return route(String(input), init) ?? okRes({});
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

function renderPartner(path = "/partner") {
  window.history.pushState({}, "", path);
  return render(<PartnerApp />);
}

beforeEach(() => {
  sessionStorage.clear();
  localStorage.clear();
  clearPartnerKey();
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

// ---------- Список лотов с индикатором чека ----------

describe("Чеки: список своих лотов (07 §3)", () => {
  it("два лота: «Чек есть» (71) и «Чека нет» (72) — GET inspections параллельно", async () => {
    const fetchMock = mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/inspections");

    expect(await screen.findByText("Kia K5, 2022")).toBeTruthy();
    expect(screen.getByText("Hyundai Sonata, 2021")).toBeTruthy();
    // индикаторы подтянулись отдельными GET (404 для 72 → «Чека нет»)
    expect(await screen.findByText("Чек есть")).toBeTruthy();
    expect(await screen.findByText("Чека нет")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Открыть чек" })).toHaveLength(2);

    const inspGets = fetchMock.mock.calls.filter(
      ([u, i]) => /\/api\/partner\/listings\/\d+\/inspection$/.test(String(u)) && !i?.method,
    );
    expect(inspGets.map(([u]) => String(u))).toEqual([
      "/api/partner/listings/71/inspection",
      "/api/partner/listings/72/inspection",
    ]);
  });

  it("пустой список лотов — EmptyBlock с подсказкой про «Объявления»", async () => {
    mockFetch(inspRoutes({ lots: [] }));
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/inspections");

    expect(await screen.findByText("Своих лотов нет")).toBeTruthy();
    expect(screen.getByText(/Разместите лот в разделе «Объявления»/)).toBeTruthy();
  });

  it("навигация: пункт «Чеки и история» после «Объявлений»", async () => {
    mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/inspections");

    await screen.findByText("Kia K5, 2022");
    expect(screen.getAllByText("Чеки и история").length).toBeGreaterThan(0);
    expect(await screen.findByRole("heading", { name: "Чеки и история" })).toBeTruthy();
  });
});

// ---------- Редактор: карта повреждений ----------

/** Открыть редактор лота 72 (без чека) и дождаться формы. */
async function openEditorFor72() {
  renderPartner("/partner/inspections");
  fireEvent.click((await screen.findAllByRole("button", { name: "Открыть чек" }))[1]);
  expect(await screen.findByRole("heading", { name: "Hyundai Sonata, 2021" })).toBeTruthy();
  await screen.findByText("Шапка чека");
}

describe("Чеки: редактор — карта повреждений (07 §3)", () => {
  it("клик зоны (hotspot по мини-карте) → тип + заметка → mark в стейте и пин на схеме", async () => {
    mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    await openEditorFor72();

    // пина ещё нет — только прозрачная тач-зона
    expect(screen.queryByRole("button", { name: "Повреждения: Капот" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Зона: Капот" }));

    // панель зоны: тип повреждения + заметка
    expect(await screen.findByText("Капот")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("Тип повреждения"), { target: { value: "dent" } });
    fireEvent.change(screen.getByLabelText("Заметка повреждения"), {
      target: { value: "вмятина после парковки" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить повреждение" }));

    // mark появился: строка в панели + пин со счётчиком 1
    const noteCell = await screen.findByText(/вмятина после парковки/);
    expect(noteCell.closest(".pa-insp-markrow")?.textContent).toContain("Вмятина");
    const pin = screen.getByRole("button", { name: "Повреждения: Капот" });
    expect(pin.textContent).toBe("1");
    expect(screen.getByText("Отметок на карте: 1")).toBeTruthy();
  });

  it("клик пина → изменить и удалить отметку", async () => {
    mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    await openEditorFor72();

    fireEvent.click(screen.getByRole("button", { name: "Зона: Капот" }));
    fireEvent.change(screen.getByLabelText("Тип повреждения"), { target: { value: "scratch" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить повреждение" }));
    expect(screen.getByRole("button", { name: "Повреждения: Капот" }).textContent).toBe("1");

    // изменить: тип меняется, пин остаётся
    fireEvent.click(screen.getByRole("button", { name: "Изменить повреждение 1" }));
    fireEvent.change(screen.getByLabelText("Тип повреждения"), { target: { value: "dent" } });
    fireEvent.change(screen.getByLabelText("Заметка повреждения"), {
      target: { value: "наждачка" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить повреждение" }));
    const edited = await screen.findByText(/наждачка/);
    expect(edited.closest(".pa-insp-markrow")?.textContent).toContain("Вмятина");
    expect(edited.closest(".pa-insp-markrow")?.textContent).not.toContain("Царапина");

    // удалить: пин исчезает
    fireEvent.click(screen.getByRole("button", { name: "Удалить повреждение 1" }));
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Повреждения: Капот" })).toBeNull(),
    );
    expect(screen.getByText("Карта без отметок — машина чистая")).toBeTruthy();
  });
});

// ---------- Чек-лист, шапка и сохранение чека ----------

describe("Чеки: чек-лист, оценка и PUT (07 §3)", () => {
  it("сегменты статусов переключаются (aria-pressed)", async () => {
    mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    await openEditorFor72();

    const defect = screen.getByRole("button", { name: "Тормозная система: Дефект" });
    expect(defect.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(defect);
    expect(defect.getAttribute("aria-pressed")).toBe("true");
    // заметка агрегата вводится свободно
    fireEvent.change(screen.getByLabelText("Заметка: Тормозная система"), {
      target: { value: "диски к замене" },
    });
  });

  it("«Сохранить чек» → PUT с marks/items/grade и тост об успехе", async () => {
    const fetchMock = mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    await openEditorFor72();

    // шапка чека
    fireEvent.change(screen.getByLabelText("Центр осмотра"), { target: { value: "АвтоТех" } });
    fireEvent.change(screen.getByLabelText("Оценка состояния"), { target: { value: "good" } });
    // отметка на карте: Капот — вмятина (pos из мини-карты _ZONE_POS)
    fireEvent.click(screen.getByRole("button", { name: "Зона: Капот" }));
    fireEvent.change(screen.getByLabelText("Тип повреждения"), { target: { value: "dent" } });
    fireEvent.change(screen.getByLabelText("Заметка повреждения"), {
      target: { value: "вмятина после парковки" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить повреждение" }));
    // чек-лист: двигатель — внимание
    fireEvent.click(screen.getByRole("button", { name: "Двигатель: Внимание" }));

    fireEvent.click(screen.getByRole("button", { name: "Сохранить чек" }));
    expect(await screen.findByText(/Чек сохранён/)).toBeTruthy();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/listings/72/inspection",
      expect.objectContaining({ method: "PUT" }),
    );
    const call = fetchMock.mock.calls.find(
      ([u, i]) =>
        String(u).endsWith("/api/partner/listings/72/inspection") && i?.method === "PUT",
    );
    const body = JSON.parse(String(call?.[1]?.body));
    expect(body.inspector).toBe("АвтоТех");
    expect(body.grade).toBe("good");
    expect(body.marks).toEqual([
      { zone: "hood", type: "dent", note: "вмятина после парковки", pos: [120, 47] },
    ]);
    // чек-лист уходит целиком: 12 агрегатов, двигатель — warn
    expect(body.items).toHaveLength(12);
    expect(body.items).toContainEqual({ key: "engine", status: "warn", note: null });
    expect(body.items).toContainEqual({ key: "transmission", status: "na", note: null });
  });

  it("сохранение после правки возвращает «← К списку лотов» с зелёным индикатором", async () => {
    // сохраняем чек лота 72 (был 404) → индикатор в списке должен стать «Чек есть»
    mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/inspections");
    await screen.findAllByRole("button", { name: "Открыть чек" });
    expect(await screen.findByText("Чека нет")).toBeTruthy();

    fireEvent.click(screen.getAllByRole("button", { name: "Открыть чек" })[1]);
    await screen.findByText("Шапка чека");
    fireEvent.click(screen.getByRole("button", { name: "Сохранить чек" }));
    expect(await screen.findByText(/Чек сохранён/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "← К списку лотов" }));
    expect(await screen.findByText("Kia K5, 2022")).toBeTruthy();
    // 71 и 72 — оба с чеком (флаг обновился без перечитки GET)
    expect(screen.getAllByText("Чек есть")).toHaveLength(2);
    expect(screen.queryByText("Чека нет")).toBeNull();
  });
});

// ---------- Загрузка существующего чека ----------

describe("Чеки: существующий чек загружается в редактор (07 §3)", () => {
  it("шапка/оценка/дата из GET, пин отметки, чек-лист со статусами", async () => {
    mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/inspections");
    fireEvent.click((await screen.findAllByRole("button", { name: "Открыть чек" }))[0]);

    expect(await screen.findByDisplayValue("АвтоТех, Сеул")).toBeTruthy();
    expect((screen.getByLabelText("Дата осмотра") as HTMLInputElement).value).toBe("2026-10-20");
    expect((screen.getByLabelText("Оценка состояния") as HTMLSelectElement).value).toBe("good");
    expect(screen.getByDisplayValue(/Состояние хорошее/)).toBeTruthy();
    // существующая отметка — пин на капоте
    expect(screen.getByRole("button", { name: "Повреждения: Капот" }).textContent).toBe("1");
    // чек-лист: двигатель — дефект (кнопка pressed)
    expect(
      screen.getByRole("button", { name: "Двигатель: Дефект" }).getAttribute("aria-pressed"),
    ).toBe("true");
    expect(screen.getByDisplayValue("потёрт ремень")).toBeTruthy();

    // клик по пину открывает панель с деталями отметки
    fireEvent.click(screen.getByRole("button", { name: "Повреждения: Капот" }));
    const mark71 = await screen.findByText(/полировка/);
    expect(mark71.closest(".pa-insp-markrow")?.textContent).toContain("Царапина");
  });

  it("фото-ссылки существующего чека перечислены, добавление некорректной — тост", async () => {
    mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/inspections");
    fireEvent.click((await screen.findAllByRole("button", { name: "Открыть чек" }))[0]);
    await screen.findByText("Фото чека");

    expect(screen.getByText("https://img.example/f1.jpg")).toBeTruthy();
    // не-URL отклоняется локально, PUT не уходит
    fireEvent.change(screen.getByLabelText("Ссылка на фото"), {
      target: { value: "не-ссылка" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить фото" }));
    expect(await screen.findByText("Ссылка должна начинаться с http:// или https://")).toBeTruthy();
    // валидная ссылка добавляется строкой с удалением
    fireEvent.change(screen.getByLabelText("Ссылка на фото"), {
      target: { value: "https://img.example/f2.jpg" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Добавить фото" }));
    expect(screen.getByText("https://img.example/f2.jpg")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Удалить фото 2" }));
    await waitFor(() =>
      expect(screen.queryByText("https://img.example/f2.jpg")).toBeNull(),
    );
  });
});

// ---------- История машины ----------

describe("История машины: таймлайн событий (07 §3)", () => {
  it("существующие события из owner_history редактируются и сохраняются через PUT", async () => {
    const fetchMock = mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/inspections");
    fireEvent.click((await screen.findAllByRole("button", { name: "Открыть чек" }))[0]);
    await screen.findByText("История машины");

    // событие из публичной карточки (GET /api/listings/71 → owner_history)
    expect(
      await screen.findByDisplayValue("Пригон из Кореи"),
    ).toBeTruthy();
    expect((screen.getAllByLabelText("Дата события")[0] as HTMLInputElement).value).toBe(
      "2023-05-01",
    );

    // правка заметки + сохранение всего таймлайна
    fireEvent.change(screen.getAllByLabelText("Заметка события")[0], {
      target: { value: "аукцион Busan" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить историю" }));
    expect(await screen.findByText(/История сохранена/)).toBeTruthy();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/listings/71/history",
      expect.objectContaining({ method: "PUT" }),
    );
    const call = fetchMock.mock.calls.find(
      ([u, i]) => String(u).endsWith("/api/partner/listings/71/history") && i?.method === "PUT",
    );
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      events: [{ date: "2023-05-01", title: "Пригон из Кореи", note: "аукцион Busan" }],
    });
  });

  it("«Добавить событие» → строка с сегодняшней датой → PUT events; пустой заголовок блокирует PUT", async () => {
    const fetchMock = mockFetch(inspRoutes());
    setPartnerKey("pi_ok", false);
    await openEditorFor72();

    expect(screen.getByText("Событий пока нет — добавьте первое")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Добавить событие" }));

    const dates = screen.getAllByLabelText("Дата события");
    const titles = screen.getAllByLabelText("Заголовок события");
    expect(dates).toHaveLength(1);
    expect(dates[0].value).toBe("2026-10-25"); // сегодня (fake timers)

    // пустой заголовок: PUT не уходит, тост с подсказкой
    fireEvent.click(screen.getByRole("button", { name: "Сохранить историю" }));
    expect(await screen.findByText("У каждого события нужны дата и заголовок")).toBeTruthy();
    expect(
      fetchMock.mock.calls.filter(
        ([u, i]) => String(u).endsWith("/history") && i?.method === "PUT",
      ),
    ).toHaveLength(0);

    // заполнили — PUT уходит с телом events
    fireEvent.change(titles[0], { target: { value: "ТО у дилера" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить историю" }));
    expect(await screen.findByText(/История сохранена/)).toBeTruthy();
    const call = fetchMock.mock.calls.find(
      ([u, i]) => String(u).endsWith("/api/partner/listings/72/history") && i?.method === "PUT",
    );
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({
      events: [{ date: "2026-10-25", title: "ТО у дилера", note: null }],
    });

    // удаление события
    fireEvent.click(screen.getByRole("button", { name: "Удалить событие 1" }));
    expect(screen.getByText("Событий пока нет — добавьте первое")).toBeTruthy();
  });
});

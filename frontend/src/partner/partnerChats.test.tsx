/**
 * Интеграционные тесты F2 (05-chats-marketplace.md §5/§7): партнёрские
 * чаты и витрина. HTTP мокается на уровне fetch (реальный partner/api.ts
 * с X-API-Key участвует в тесте):
 * - контракты /api/partner/chats* (треды/сообщения/read/ws-token/OG) и
 *   /api/partner/offers (GET/POST/DELETE) — URL, метод, тело, заголовок;
 * - навигация: разделы «Чаты» (бейдж unread = poll /api/partner/chats)
 *   и «Витрина»;
 * - экран «Чаты»: список seller/deal-тредов (реуз ChatList) → чат → назад;
 * - «Витрина»: список офферов, добавление (POST), удаление (DELETE), пусто.
 *
 * Транспорт чата замокан (createChatTransport → фейк): сокет в jsdom
 * не подключается, поведение транспорта покрыто chatSocket.test.ts.
 */
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatEvent } from "../lib/chatSocket";

const mocks = vi.hoisted(() => ({
  handlers: [] as Array<(ev: ChatEvent) => void>,
  subscriptions: [] as string[],
  createTransport: vi.fn(),
}));

vi.mock("../lib/chatSocket", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../lib/chatSocket")>()),
  createChatTransport: mocks.createTransport,
}));

import { PartnerApp } from "./PartnerApp";
import {
  addPartnerOffer,
  clearPartnerKey,
  fetchPartnerChats,
  fetchPartnerLinkPreview,
  fetchPartnerMessages,
  fetchPartnerOffers,
  fetchPartnerWsToken,
  markPartnerRead,
  removePartnerOffer,
  sendPartnerMessage,
  setPartnerKey,
  toPartnerThread,
} from "./api";

// ---------- Данные и моки ----------

const NOW = Date.parse("2026-10-25T12:00:00Z");
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

/** Сырой ThreadOut бэка (routers/market.py): subject.id, last_message.sender. */
const CHATS_RAW = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    type: "seller",
    title: "Kia K5 — вопрос по лоту",
    status: "active",
    unread: 2,
    last_message: {
      body: "ещё актуально?",
      created_at: new Date(NOW - 5 * MIN).toISOString(),
      sender: "user",
      sender_id: 148812345,
    },
    subject: {
      id: 55,
      make: "Kia",
      model: "K5",
      year: 2022,
      price: 21_300_000,
      currency: "KRW",
      url: "https://encar.com/x",
      image: null,
    },
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    type: "deal",
    title: "Агент на сделку — Sonata",
    status: "active",
    unread: 5,
    last_message: {
      body: "агент подключится",
      created_at: new Date(NOW - 15 * MIN).toISOString(),
      sender: "system",
      sender_id: null,
    },
    subject: { id: 77, make: "Hyundai", model: "Sonata", year: 2021, price: 17_000_000, currency: "KRW", url: null, image: null },
  },
];

const MSGS = [
  { id: 10, sender_type: "partner", sender_id: 1, body: "**готово** к показу", created_at: new Date(NOW - 6 * MIN).toISOString() },
  { id: 11, sender_type: "user", sender_id: 148812345, body: "а когда можно посмотреть?", created_at: new Date(NOW - 5 * MIN).toISOString() },
];

/** Сырой MarketOfferOut бэка (note + карточка лота). */
const OFFERS_RAW = [
  {
    listing_id: 55,
    partner_id: 1,
    note: "торг возможен",
    make: "Kia",
    model: "K5",
    year: 2022,
    price: 21_300_000,
    currency: "KRW",
    mileage_km: 30_000,
    url: "https://encar.com/x",
    image: null,
    has_market: true,
  },
];

function okRes(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}
function failRes(status: number, detail: string): Response {
  return { ok: false, status, json: async () => ({ detail }) } as Response;
}

type RouteFn = (url: string, init?: RequestInit) => Response | undefined;

function defaultRoutes(): RouteFn {
  // Обзор партнёра ждёт: /me — профиль, /leads — список, /market — {items}
  return (url) => {
    if (url.includes("/leads")) return okRes([]);
    if (url.includes("/market")) return okRes({ items: [] });
    return okRes(ME);
  };
}

/** Маршруты чат-контура партнёра (сверху — специфичные пути). */
function chatRoutes(over: { chats?: unknown } = {}): RouteFn {
  return (url, init) => {
    if (url.endsWith("/api/partner/chats/ws-token")) return okRes({ token: "jwt", url: "ws://c" });
    if (url.includes("/api/partner/chats/") && url.includes("/read")) {
      return okRes({ last_read_message_id: 11 });
    }
    if (url.includes("/api/partner/chats/") && url.includes("/messages") && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { body: string };
      return okRes({ id: 12, sender_type: "partner", sender_id: 1, body: body.body, created_at: new Date(NOW).toISOString() });
    }
    if (url.includes("/api/partner/chats/")) return okRes(MSGS);
    if (url.endsWith("/api/partner/chats")) return okRes(over.chats ?? CHATS_RAW);
    return defaultRoutes()(url);
  };
}

function mockFetch(route: RouteFn) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    // Обзор ждёт списки: непокрытые пути-списки дефолтим пустым массивом
    if (route(url, init)) return route(url, init);
    if (/\/leads/.test(url)) return okRes([]);
    return okRes({});
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

/** Фейк-транспорт: захват обработчиков + подписки (как ChatRoom.test). */
function fakeTransport() {
  return {
    kind: "ws" as const,
    connect: async () => undefined,
    subscribe: (ch: string) => mocks.subscriptions.push(ch),
    unsubscribe: () => undefined,
    onEvent: (h: (ev: ChatEvent) => void) => {
      mocks.handlers.push(h);
      return () => undefined;
    },
    disconnect: () => undefined,
  };
}

function renderPartner(path = "/partner") {
  window.history.pushState({}, "", path);
  return render(<PartnerApp />);
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.handlers.length = 0;
  mocks.subscriptions.length = 0;
  mocks.createTransport.mockImplementation(async () => fakeTransport());
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

// ---------- Контракты чатов (§5) ----------

describe("partner/api: контракты чатов", () => {
  beforeEach(() => setPartnerKey("pi_key2", false));

  it("GET /chats — X-API-Key, нормализация subject.id→listing_id и sender→sender_type", async () => {
    const fetchMock = mockFetch(chatRoutes());
    const threads = await fetchPartnerChats();

    // papi ходит с ключом кабинета (X-API-Key, 04 §4.1)
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/chats",
      expect.objectContaining({ headers: expect.objectContaining({ "X-API-Key": "pi_key2" }) }),
    );
    expect(threads).toHaveLength(2);

    const seller = threads.find((t) => t.type === "seller");
    expect(seller?.id).toBe("11111111-1111-1111-1111-111111111111");
    // бэк отдаёт subject.id — ядро F1 (ChatList/шапка чата) ждёт listing_id
    expect(seller?.subject?.listing_id).toBe(55);
    expect(seller?.subject?.make).toBe("Kia");
    // бэк отдаёт last_message.sender — приводим к sender_type ядра
    expect(seller?.last_message?.sender_type).toBe("user");
    expect(seller?.last_message?.body).toBe("ещё актуально?");
    expect(seller?.unread).toBe(2);
    expect(threads.find((t) => t.type === "deal")?.type).toBe("deal");
  });

  it("GET …/messages — плоский list[MessageOut] → {items, has_more}", async () => {
    const fetchMock = mockFetch(chatRoutes());
    const page = await fetchPartnerMessages("t1", { beforeId: 5 });

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/chats/t1/messages?before_id=5&limit=50",
      expect.anything(),
    );
    expect(page.items.map((m) => m.id)).toEqual([10, 11]);
    expect(page.items[0].sender_type).toBe("partner");
    expect(page.has_more).toBe(false); // 2 < limit

    // страница полная (вернули limit) — вероятно, есть ещё
    const full = await fetchPartnerMessages("t1", { limit: 2 });
    expect(full.has_more).toBe(true);
  });

  it("POST …/messages {body} и POST …/read {last_read_message_id}", async () => {
    const fetchMock = mockFetch(chatRoutes());
    const sent = await sendPartnerMessage("t1", "да, приезжайте");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/chats/t1/messages",
      expect.objectContaining({
        method: "POST",
        headers: expect.objectContaining({ "X-API-Key": "pi_key2" }),
        body: JSON.stringify({ body: "да, приезжайте" }),
      }),
    );
    expect(sent.id).toBe(12);

    await markPartnerRead("t1", 11);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/chats/t1/read",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ last_read_message_id: 11 }) }),
    );
  });

  it("GET …/ws-token → {token, url} — JWT кабинета (sub=partner:{id})", async () => {
    const fetchMock = mockFetch(chatRoutes());
    const t = await fetchPartnerWsToken();
    expect(fetchMock).toHaveBeenCalledWith("/api/partner/chats/ws-token", expect.anything());
    expect(t).toEqual({ token: "jwt", url: "ws://c" });
  });

  it("OG-превью: партнёрское зеркало link-preview, 404 → null (плейсхолдер)", async () => {
    const URL_ = "https://encar.com/car/12345";
    const fetchMock = mockFetch((url) =>
      url.includes("/link-preview")
        ? okRes({ kind: "external", title: "Kia K5", description: "состояние", image: null, site_name: "Encar", domain: "encar.com", url: URL_ })
        : okRes({}),
    );
    const data = await fetchPartnerLinkPreview("t1", URL_);
    expect(fetchMock).toHaveBeenCalledWith(
      `/api/partner/chats/t1/link-preview?url=${encodeURIComponent(URL_)}`,
      expect.anything(),
    );
    expect(data?.title).toBe("Kia K5");
    expect(data?.domain).toBe("encar.com");

    mockFetch((url) => (url.includes("/link-preview") ? failRes(404, "no mirror yet") : okRes({})));
    await expect(fetchPartnerLinkPreview("t9", "https://x.io/y")).resolves.toBeNull();
  });

  it("toPartnerThread: битый сырой ответ не роняет экран", () => {
    const t = toPartnerThread({ id: "x", type: "weird", unread: "3" });
    expect(t.type).toBe("seller"); // неизвестный тип — базовый seller
    expect(t.unread).toBe(3);
    expect(t.last_message).toBeNull();
    expect(t.subject).toBeNull();
  });
});

// ---------- Контракты витрины ----------

describe("partner/api: контракты витрины", () => {
  beforeEach(() => setPartnerKey("pi_key2", false));

  it("GET /offers — нормализация MarketOfferOut (note + карточка лота)", async () => {
    const fetchMock = mockFetch((url) => (url.endsWith("/api/partner/offers") ? okRes(OFFERS_RAW) : okRes({})));
    const offers = await fetchPartnerOffers();

    expect(fetchMock).toHaveBeenCalledWith("/api/partner/offers", expect.anything());
    expect(offers).toHaveLength(1);
    expect(offers[0].listing_id).toBe(55);
    expect(offers[0].note).toBe("торг возможен");
    expect(offers[0].make).toBe("Kia");
    expect(offers[0].price).toBe(21_300_000);
    expect(offers[0].currency).toBe("KRW");
  });

  it("POST /offers {listing_id, note} (201/200 reuse) и DELETE /offers/{listing_id}", async () => {
    const fetchMock = mockFetch((url, init) => {
      if (url.endsWith("/api/partner/offers") && init?.method === "POST") return okRes({ listing_id: 77 });
      if (url === "/api/partner/offers/77" && init?.method === "DELETE") return okRes({ removed: true });
      return okRes([]);
    });

    const res = await addPartnerOffer(77, "свежая");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/offers",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ listing_id: 77, note: "свежая" }) }),
    );
    expect(res.listing_id).toBe(77);

    await removePartnerOffer(77);
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/partner/offers/77",
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});

// ---------- Навигация: «Чаты» с бейджем, «Витрина» ----------

describe("навигация: разделы «Чаты» и «Витрина»", () => {
  it("«Чаты» в сайдбаре и таб-баре; «Ещё» — кнопка шторки (NAV-V2 §3)", async () => {
    mockFetch(chatRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner();

    await screen.findByRole("heading", { name: "Обзор" });
    // «Чаты» — в сайдбаре и таб-баре (таб-бар: Обзор/Пул/Мои/Чаты/Ещё)
    expect(screen.getAllByText("Чаты").length).toBeGreaterThanOrEqual(2);
    const tabbar = document.querySelector(".pa-tabbar") as HTMLElement;
    const labels = Array.from(
      tabbar.querySelectorAll(".pa-tab__label"),
    ).map((el) => el.textContent);
    expect(labels).toEqual(["Обзор", "Пул", "Мои", "Чаты", "Ещё"]);
    // «Ещё» — кнопка с popup-шторкой (группа: рынок/витрина/…)
    const moreBtn = tabbar.querySelector(
      'button[aria-label="Ещё"]',
    ) as HTMLElement;
    expect(moreBtn.getAttribute("aria-haspopup")).toBe("dialog");
    // «Витрина» доступна из сайдбара (в шторке — те же MORE_ITEMS)
    expect(screen.getAllByText("Витрина").length).toBeGreaterThanOrEqual(1);

    // бейдж «Чаты»: 2 + 5 непрочитанных (GET /api/partner/chats)
    await waitFor(() => {
      const nav = screen.getAllByText("Чаты")[0].closest("a");
      expect(nav?.querySelector(".pa-navbadge")?.textContent).toBe("7");
    });
  });

  it("бейдж гаснет, когда непрочитанных нет", async () => {
    mockFetch(chatRoutes({ chats: [{ ...CHATS_RAW[0], unread: 0 }] }));
    setPartnerKey("pi_ok", false);
    renderPartner();

    await screen.findByRole("heading", { name: "Обзор" });
    await waitFor(() => {
      const nav = screen.getAllByText("Чаты")[0].closest("a");
      expect(nav?.querySelector(".pa-navbadge")).toBeNull();
    });
  });
});

// ---------- Экран «Чаты» ----------

describe("экран «Чаты»: список тредов → чат → назад", () => {
  it("список seller/deal тредов (тип-бейдж, сниппет, unread) и переход в чат", async () => {
    const fetchMock = mockFetch(chatRoutes());
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/chats");

    // треды из GET /api/partner/chats: заголовки, подписи типов, сниппеты
    expect(await screen.findByText("Kia K5 — вопрос по лоту")).toBeTruthy();
    expect(screen.getByText("Агент на сделку — Sonata")).toBeTruthy();
    expect(screen.getByText("Продавец")).toBeTruthy();
    expect(screen.getByText("Сделка")).toBeTruthy();
    expect(screen.getByText("ещё актуально?")).toBeTruthy(); // сниппет последнего
    // unread-бейджи строк списка: 2 и 5
    const unreads = Array.from(document.querySelectorAll(".chat-item__unread")).map((el) => el.textContent);
    expect(unreads).toContain("2");
    expect(unreads).toContain("5");

    // выбор треда → overlay-чат (реюз ядра: свои=partner справа)
    fireEvent.click(screen.getByRole("button", { name: "Чат: Kia K5 — вопрос по лоту" }));
    expect(await screen.findByTestId("partner-chat-room")).toBeTruthy();
    await waitFor(() =>
      expect(document.querySelector('.chat-msg--own[data-id="10"]')).not.toBeNull(),
    );
    expect(document.querySelector('.chat-msg--other[data-id="11"]')).not.toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/partner\/chats\/11111111-1111-1111-1111-111111111111\/messages\?limit=50$/),
      expect.anything(),
    );
    // транспорт подписан на канал треда с партнёрским ws-токеном (§4)
    await waitFor(() => expect(mocks.subscriptions).toContain("thread:11111111-1111-1111-1111-111111111111"));

    // назад — к списку
    fireEvent.click(screen.getByRole("button", { name: "К списку чатов" }));
    await waitFor(() => expect(screen.queryByTestId("partner-chat-room")).toBeNull());
    expect(screen.getByText("Kia K5 — вопрос по лоту")).toBeTruthy();
  });

  it("пусто: пояснение, откуда берутся чаты (витрина + заявки на агента)", async () => {
    mockFetch(chatRoutes({ chats: [] }));
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/chats");

    expect(await screen.findByText("Чатов пока нет")).toBeTruthy();
    expect(screen.getByText(/Внутреннем рынке/)).toBeTruthy();
    expect(screen.getByText(/заявке на агента/)).toBeTruthy();
  });
});

// ---------- Экран «Витрина» ----------

describe("экран «Витрина»: офферы счёта", () => {
  it("список офферов (note, id, цена), удаление DELETE и добавление POST", async () => {
    // мутируемый стейт витрины: POST добавляет, DELETE снимает
    let offers: unknown[] = [OFFERS_RAW[0]];
    const fetchMock = mockFetch((url, init) => {
      if (url.endsWith("/api/partner/offers")) {
        if (init?.method === "POST") {
          offers = [
            ...offers,
            { listing_id: 77, note: "свежая", make: "Kia", model: "Sportage", year: 2023 },
          ];
          return okRes({ listing_id: 77 });
        }
        return okRes(offers);
      }
      if (url === "/api/partner/offers/55" && init?.method === "DELETE") {
        offers = offers.filter((o) => (o as { listing_id: number }).listing_id !== 55);
        return okRes({ removed: true });
      }
      return defaultRoutes()(url);
    });
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/showcase");

    // карточка оффера: имя, id, цена, note
    expect(await screen.findByText("Kia K5 2022")).toBeTruthy();
    // meta одной строкой: "#55 · 21,3 млн KRW" (PartnerScreens pa-offer__meta)
    expect(screen.getByText(/#55 · 21,3 млн KRW/)).toBeTruthy();
    expect(screen.getByText("торг возможен")).toBeTruthy();

    // убрать: DELETE /api/partner/offers/55, список перезагружается — пусто
    fireEvent.click(screen.getByRole("button", { name: "Убрать" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/offers/55",
        expect.objectContaining({ method: "DELETE" }),
      ),
    );
    expect(await screen.findByText("Витрина пуста")).toBeTruthy();

    // добавить: POST {listing_id, note}, новый оффер появляется
    fireEvent.change(screen.getByLabelText("id лота"), { target: { value: "77" } });
    fireEvent.change(screen.getByLabelText("Заметка"), { target: { value: "свежая" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/offers",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ listing_id: 77, note: "свежая" }) }),
      ),
    );
    expect((await screen.findAllByText("Kia Sportage 2023")).length).toBeGreaterThanOrEqual(1);
  });

  it("пусто: пояснение зачем витрина + пре-валидация id (POST не уходит)", async () => {
    const fetchMock = mockFetch((url) => (url.endsWith("/api/partner/offers") ? okRes([]) : defaultRoutes()(url)));
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/showcase");

    expect(await screen.findByText("Витрина пуста")).toBeTruthy();
    expect(screen.getByText(/написать вам как продавцу/)).toBeTruthy();
    // подсказка, где взять id
    expect(screen.getByText("id виден в ссылке карточки лота: /listing/N")).toBeTruthy();

    // невалидный id — тост с подсказкой, запроса нет
    fireEvent.change(screen.getByLabelText("id лота"), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Добавить" }));
    await screen.findByText("id лота — целое число больше нуля");
    expect(
      fetchMock.mock.calls.filter(([u, i]) => String(u).endsWith("/api/partner/offers") && i?.method === "POST"),
    ).toHaveLength(0);
  });
});

// ---------- Сделки ждут отметки: CTA «Отметить сделкой» (долг №6) ----------

/** Deal-тред с лидом: raw ThreadOut теперь отдаёт lead_id + lead_status. */
const DEAL_RAW = {
  id: "33333333-3333-3333-3333-333333333333",
  type: "deal",
  title: "Агент на сделку — Sonata",
  status: "active",
  unread: 1,
  lead_id: 9001,
  lead_status: "claimed",
  last_message: null,
  subject: null,
};

describe("экран «Чаты»: CTA «Отметить сделкой» в deal-треде", () => {
  it("кнопка только у deal-треда без статуса deal: подтверждение → POST feedback → тост", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    const fetchMock = mockFetch(
      chatRoutes({ chats: [CHATS_RAW[0], { ...DEAL_RAW }, { ...DEAL_RAW, id: "44444444-4444-4444-4444-444444444444", lead_id: 9002, lead_status: "deal" }] }),
    );
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/chats");

    // ровно одна кнопка: seller-тред и уже отмеченный deal (lead_status=deal) — без CTA
    const cta = await screen.findByRole("button", { name: "Отметить сделкой" });
    expect(screen.getAllByRole("button", { name: "Отметить сделкой" })).toHaveLength(1);
    expect(confirmSpy).not.toHaveBeenCalled();

    fireEvent.click(cta);
    expect(confirmSpy).toHaveBeenCalledWith("Сделка завершена?");
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/leads/9001/feedback",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({ "X-API-Key": "pi_ok" }),
          body: JSON.stringify({ status: "deal" }),
        }),
      ),
    );
    // тост об успехе и CTA погашен (локальный lead_status=deal)
    expect(await screen.findByText(/Сделка отмечена/)).toBeTruthy();
    await waitFor(() =>
      expect(screen.queryByRole("button", { name: "Отметить сделкой" })).toBeNull(),
    );
    confirmSpy.mockRestore();
  });

  it("отмена подтверждения — POST не уходит; без lead_id кнопки нет", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(false);
    const fetchMock = mockFetch(chatRoutes({ chats: [CHATS_RAW[1]] })); // deal без lead_id
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/chats");

    expect(await screen.findByText("Агент на сделку — Sonata")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Отметить сделкой" })).toBeNull();
    expect(confirmSpy).not.toHaveBeenCalled();

    expect(
      fetchMock.mock.calls.filter(([u, i]) => String(u).includes("/feedback") && i?.method === "POST"),
    ).toHaveLength(0);
    confirmSpy.mockRestore();
  });

  it("ошибка фидбека — тост с ошибкой, CTA остаётся", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    mockFetch((url, init) => {
      if (url.includes("/feedback") && init?.method === "POST") return failRes(404, "no lead");
      return chatRoutes({ chats: [DEAL_RAW] })(url, init);
    });
    setPartnerKey("pi_ok", false);
    renderPartner("/partner/chats");

    fireEvent.click(await screen.findByRole("button", { name: "Отметить сделкой" }));
    expect(await screen.findByText("Не удалось отметить сделку — попробуйте ещё раз")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Отметить сделкой" })).toBeTruthy();
    confirmSpy.mockRestore();
  });
});

// ---------- Idempotency-Key отправки (долг №7) ----------

describe("sendPartnerMessage: заголовок Idempotency-Key", () => {
  beforeEach(() => setPartnerKey("pi_key2", false));

  it("каждая отправка несёт уникальный uuid (защита от ретраев прокси)", async () => {
    const fetchMock = mockFetch(chatRoutes());
    const headersOf = () =>
      fetchMock.mock.calls
        .filter(([u, i]) => String(u).includes("/messages") && i?.method === "POST")
        .map(([, i]) => ((i as RequestInit).headers as Record<string, string>)?.["Idempotency-Key"]);
    await sendPartnerMessage("t1", "первое");
    await sendPartnerMessage("t1", "второе");
    const keys = headersOf();
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(keys[0]).not.toBe(keys[1]);
  });
});

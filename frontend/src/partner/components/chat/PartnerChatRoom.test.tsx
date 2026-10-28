/**
 * Тесты партнёрского чата (PartnerChatRoom, F2 — 05 §7):
 * - «свои» = сообщения партнёра (sender_type='partner') справа с ✓/✓✓,
 *   юзер («Клиент») — слева, bot/system — центр-строки;
 * - markdown в пузырях (lib/markdown из ядра);
 * - typing-индикатор и read-события юзера (свои — игнорируются);
 * - отправка по Enter — REST POST /api/partner/chats/{id}/messages;
 * - OG-превью внешней ссылки: GET …/link-preview?url=ENC → карточка;
 * - транспорт: createChatTransport с партнёрским ws-токеном (мок —
 *   захват onEvent/subscribe; поведение транспорта — chatSocket.test);
 * - «📄 Выжимка» (12-ai-trust §3): POST …/summary → блок с текстом
 *   и «по состоянию на …», повторный клик обновляет.
 *
 * HTTP мокается на уровне fetch: реальный partner/api.ts (papi, X-API-Key)
 * участвует в тесте — как в partner.test.tsx.
 */
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ChatEvent, ChatTransport } from "../../../lib/chatSocket";

const mocks = vi.hoisted(() => ({
  handlers: [] as Array<(ev: ChatEvent) => void>,
  subscriptions: [] as string[],
  createTransport: vi.fn(),
}));

// Транспорт замокан (centrifuge в jsdom не подключается), остальное — оригинал
vi.mock("../../../lib/chatSocket", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../../lib/chatSocket")>()),
  createChatTransport: mocks.createTransport,
}));

import { PartnerChatRoom } from "./PartnerChatRoom";
import { setPartnerKey, type PartnerThread } from "../../api";

const NOW = Date.parse("2026-10-25T12:00:00Z");
const ISO = new Date(NOW - 5 * 60_000).toISOString();

const thread: PartnerThread = {
  id: "t1",
  type: "seller",
  title: "Kia K5",
  status: "active",
  unread: 0,
  last_message: null,
  subject: {
    listing_id: 55,
    make: "Kia",
    model: "K5",
    year: 2022,
    price: 21_300_000,
    currency: "KRW",
    image: null,
  },
};

const msg = (id: number, sender_type: string, body: string) => ({
  id,
  sender_type,
  sender_id: null,
  body,
  created_at: ISO,
});

const MESSAGES = [
  msg(10, "partner", "**готово** к показу"),
  msg(11, "user", "а когда можно посмотреть?"),
  msg(12, "system", "Заявка на агента создана"),
];

function okRes(body: unknown): Response {
  return { ok: true, status: 200, json: async () => body } as Response;
}
function failRes(status: number, detail: string): Response {
  return { ok: false, status, json: async () => ({ detail }) } as Response;
}

type RouteFn = (url: string, init?: RequestInit) => Response | undefined;

/** Маршруты чат-контура партнёра (routers/market.py). */
function chatRoutes(extra: RouteFn = () => undefined): RouteFn {
  return (url, init) => {
    const r = extra(url, init);
    if (r) return r;
    if (url.includes("/messages") && init?.method === "POST") {
      const body = JSON.parse(String(init.body)) as { body: string };
      return okRes(msg(13, "partner", body.body));
    }
    if (url.includes("/read")) return okRes({ last_read_message_id: 12 });
    if (url.endsWith("/ws-token")) return okRes({ token: "jwt", url: "ws://c" });
    if (url.includes("/messages")) return okRes(MESSAGES);
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

/** Фейк-транспорт: захват onEvent/subscribe (события стреляем руками). */
function fakeTransport(): ChatTransport {
  return {
    kind: "ws",
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

/** Стрельнуть событием транспорта и сбросить React-очередь обновлений. */
async function fire(ev: ChatEvent): Promise<void> {
  await act(async () => {
    mocks.handlers.forEach((h) => h(ev));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  mocks.handlers.length = 0;
  mocks.subscriptions.length = 0;
  mocks.createTransport.mockImplementation(async () => fakeTransport());
  sessionStorage.clear();
  localStorage.clear();
  setPartnerKey("pi_ok", false);
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

const renderRoom = (t: PartnerThread = thread) =>
  render(
    <MemoryRouter>
      <PartnerChatRoom threadId={t.id} thread={t} onBack={() => undefined} />
    </MemoryRouter>,
  );

// ---------- Лента: стороны, markdown, read-маркеры ----------

describe("PartnerChatRoom: лента сообщений", () => {
  it("свои (partner) справа с ✓, юзер («Клиент») слева, system — центр; markdown", async () => {
    const fetchMock = mockFetch(chatRoutes());
    renderRoom();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith("/api/partner/chats/t1/messages?limit=50", expect.anything()),
    );
    await screen.findByText(/к показу/);

    // markdown: **bold** → strong (ядро lib/markdown)
    const own = document.querySelector('.chat-msg--own[data-id="10"]');
    expect(own).not.toBeNull();
    expect(own?.querySelector(".chat-md strong")?.textContent).toBe("готово");
    expect(own?.querySelector(".chat-msg__ticks")?.getAttribute("data-read")).toBe("sent");

    // юзер — слева с подписью «Клиент» (в партнёрском чате клиент — собеседник)
    const other = document.querySelector('.chat-msg--other[data-id="11"]');
    expect(other).not.toBeNull();
    expect(other?.querySelector(".chat-msg__sender")?.textContent).toBe("Клиент");

    // system — плоская центр-строка
    expect(document.querySelector(".chat-msg--system")?.textContent).toContain("Заявка на агента создана");

    // шапка: тип «Продавец», предмет лота ведёт на карточку мини-аппа
    expect(screen.getByText("Продавец")).toBeTruthy();
    expect(document.querySelector(".chat-room__subject")?.getAttribute("href")).toBe("/listing/55");

    // первое чтение: read-маркер до последнего сообщения
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/chats/t1/read",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ last_read_message_id: 12 }) }),
      ),
    );
  });

  it("read-событие юзера переводит ✓ в ✓✓; свой read-маркер игнорируется", async () => {
    mockFetch(chatRoutes());
    renderRoom();
    await screen.findByText(/к показу/);

    // чужой (partner) read — тики своих сообщений не меняет
    await fire({ type: "read", user_type: "partner", last_read_message_id: 99 });
    expect(
      document.querySelector('.chat-msg--own[data-id="10"] .chat-msg__ticks')?.getAttribute("data-read"),
    ).toBe("sent");

    // read юзера — ✓✓
    await fire({ type: "read", user_type: "user", last_read_message_id: 10 });
    await waitFor(() =>
      expect(
        document.querySelector('.chat-msg--own[data-id="10"] .chat-msg__ticks')?.getAttribute("data-read"),
      ).toBe("read"),
    );
    expect(
      document.querySelector('.chat-msg--own[data-id="10"] .chat-msg__ticks')?.textContent,
    ).toContain("✓✓");
  });
});

// ---------- Композер и транспорт ----------

describe("PartnerChatRoom: композер и транспорт", () => {
  it("Enter отправляет (POST …/messages), ответ бэка падает в ленту справа", async () => {
    const fetchMock = mockFetch(chatRoutes());
    renderRoom();
    await screen.findByText(/к показу/);

    const ta = screen.getByLabelText("Сообщение");
    fireEvent.change(ta, { target: { value: "да, приезжайте" } });
    fireEvent.keyDown(ta, { key: "Enter" });

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/chats/t1/messages",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ body: "да, приезжайте" }) }),
      ),
    );
    // ответ (sender_type='partner') — свой, справа
    await waitFor(() => expect(document.querySelector('.chat-msg--own[data-id="13"]')).not.toBeNull());
    expect((screen.getByLabelText("Сообщение") as HTMLTextAreaElement).value).toBe("");
  });

  it("Shift+Enter — перенос, не отправка", async () => {
    const fetchMock = mockFetch(chatRoutes());
    renderRoom();
    await screen.findByText(/к показу/);

    fireEvent.change(screen.getByLabelText("Сообщение"), { target: { value: "строка" } });
    fireEvent.keyDown(screen.getByLabelText("Сообщение"), { key: "Enter", shiftKey: true });
    expect(
      fetchMock.mock.calls.filter(([u, i]) => String(u).endsWith("/messages") && i?.method === "POST"),
    ).toHaveLength(0);
  });

  it("транспорт: партнёрский ws-токен (fetcher) + подписка thread:{id}", async () => {
    const fetchMock = mockFetch(chatRoutes());
    renderRoom();
    await screen.findByText(/к показу/);

    // createChatTransport получил партнёрские загрузчики (DI ядра F1)
    await waitFor(() =>
      expect(mocks.createTransport).toHaveBeenCalledWith(
        "t1",
        expect.objectContaining({
          tokenFetcher: expect.any(Function),
          polling: expect.objectContaining({ fetcher: expect.any(Function) }),
        }),
      ),
    );
    // подписка на канал треда (uuid = chat_threads.id, §4)
    await waitFor(() => expect(mocks.subscriptions).toContain("thread:t1"));

    // токен-загрузчик ходит в партнёрское зеркало ws-token (sub=partner:{id})
    const { tokenFetcher } = mocks.createTransport.mock.calls[0][1] as {
      tokenFetcher: () => Promise<unknown>;
    };
    await act(async () => {
      await tokenFetcher();
    });
    expect(fetchMock).toHaveBeenCalledWith("/api/partner/chats/ws-token", expect.anything());
  });

  it("typing юзера — индикатор «печатает»; свой typing не показываем", async () => {
    mockFetch(chatRoutes());
    renderRoom();
    await screen.findByText(/к показу/);

    await fire({ type: "typing", user_type: "partner" });
    expect(screen.queryByText(/печатает/)).toBeNull();

    await fire({ type: "typing", user_type: "user" });
    await waitFor(() => expect(screen.getByText(/печатает/)).toBeTruthy());
  });

  it("message-событие юзера дописывается без дублей и сразу читается", async () => {
    const fetchMock = mockFetch(chatRoutes());
    renderRoom();
    await screen.findByText(/к показу/);

    await fire({ type: "message", message: msg(11, "user", "а когда можно посмотреть?") }); // дубль id
    await fire({ type: "message", message: msg(14, "user", "до свидания") });
    expect(screen.getByText("до свидания")).toBeTruthy();
    expect(document.querySelectorAll('[data-id="11"]').length).toBe(1);
    // входящее чужое — тред открыт, read-маркер подняли
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/chats/t1/read",
        expect.objectContaining({ method: "POST", body: JSON.stringify({ last_read_message_id: 14 }) }),
      ),
    );
  });
});

// ---------- OG-превью внешней ссылки (F2, §8) ----------

describe("PartnerChatRoom: OG-превью внешних ссылок", () => {
  it("сообщение-ссылка → GET …/link-preview?url=ENC → карточка og.title/description/домен", async () => {
    const LINK = "https://encar.com/car/12345";
    const fetchMock = mockFetch(
      chatRoutes((url) =>
        url.includes("/messages?")
          ? okRes([msg(10, "user", LINK)])
          : url.includes("/link-preview")
            ? okRes({
                kind: "external",
                title: "Kia K5 — продажа",
                description: "Отличное состояние",
                image: null,
                site_name: "Encar",
                domain: "encar.com",
                url: LINK,
              })
            : undefined,
      ),
    );
    renderRoom();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/partner/chats/t1/link-preview?url=${encodeURIComponent(LINK)}`,
        expect.anything(),
      ),
    );
    const og = await waitFor(() => {
      const el = document.querySelector(".chat-og");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
    expect(og.textContent).toContain("Kia K5 — продажа");
    expect(og.textContent).toContain("Отличное состояние");
    expect(og.querySelector(".chat-og__domain")?.textContent).toContain("Encar");
    expect(og.getAttribute("href")).toBe(LINK);
  });

  it("бэк не отдал OG (404) — плейсхолдер-чип с доменом, без карточки", async () => {
    const LINK = "https://encar.com/car/99999";
    const fetchMock = mockFetch(
      chatRoutes((url) =>
        url.includes("/messages?")
          ? okRes([msg(10, "user", LINK)])
          : url.includes("/link-preview")
            ? failRes(404, "no mirror yet")
            : undefined,
      ),
    );
    renderRoom();

    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        `/api/partner/chats/t1/link-preview?url=${encodeURIComponent(LINK)}`,
        expect.anything(),
      ),
    );
    await act(async () => {
      await Promise.resolve();
    });
    expect(document.querySelector(".chat-og")).toBeNull();
    const chip = document.querySelector(".chat-link-chip");
    expect(chip?.textContent).toContain("encar.com");
    expect(chip?.getAttribute("href")).toBe(LINK);
  });
});

// ---------- Группа (08-group-governance §7): партнёрский минимум ----------

describe("PartnerChatRoom: group — mute/leave в шапке (08 §7)", () => {
  /** Group-тред: partner/api тип не расширяли — свободный каст. */
  const groupThread = {
    ...thread,
    id: "g1",
    type: "group",
    title: "Переговоры: Kia K5",
  } as unknown as PartnerThread;

  it("mute-колокольчик: POST …/mute → 🔕; повтор → POST …/unmute → 🔔", async () => {
    const fetchMock = mockFetch(chatRoutes());
    renderRoom(groupThread);
    await screen.findByText(/к показу/);

    const bell = screen.getByTestId("partner-mute-bell");
    expect(bell.textContent).toBe("🔔");
    fireEvent.click(bell);
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/chats/g1/mute",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() => expect(screen.getByTestId("partner-mute-bell").textContent).toBe("🔕"));

    fireEvent.click(screen.getByTestId("partner-mute-bell"));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/chats/g1/unmute",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() => expect(screen.getByTestId("partner-mute-bell").textContent).toBe("🔔"));
  });

  it("«Выйти»: POST …/leave → выход к списку (onBack)", async () => {
    const fetchMock = mockFetch(chatRoutes());
    const onBack = vi.fn();
    render(
      <MemoryRouter>
        <PartnerChatRoom threadId="g1" thread={groupThread} onBack={onBack} />
      </MemoryRouter>,
    );
    await screen.findByText(/к показу/);

    fireEvent.click(screen.getByRole("button", { name: "🚪 Выйти" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/chats/g1/leave",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    await waitFor(() => expect(onBack).toHaveBeenCalled());
  });

  it("не-group (seller): кнопок mute и «Выйти» нет", async () => {
    mockFetch(chatRoutes());
    renderRoom(thread);
    await screen.findByText(/к показу/);
    expect(screen.queryByTestId("partner-mute-bell")).toBeNull();
    expect(screen.queryByRole("button", { name: "🚪 Выйти" })).toBeNull();
  });
});

// ---------- «📄 Выжимка» диалога (12-ai-trust §3) ----------

describe("PartnerChatRoom: «📄 Выжимка» (12 §3)", () => {
  it("клик → POST …/summary → блок с текстом и «по состоянию на …»; повтор — обновление", async () => {
    let n = 0;
    const fetchMock = mockFetch(
      chatRoutes((url, init) => {
        if (url.includes("/summary") && init?.method === "POST") {
          return okRes({ summary: `выжимка №${++n}`, as_of_message_id: 12 });
        }
        return undefined;
      }),
    );
    renderRoom();
    await screen.findByText(/к показу/);
    // до клика блока нет
    expect(screen.queryByTestId("partner-chat-summary")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "📄 Выжимка" }));
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        "/api/partner/chats/t1/summary",
        expect.objectContaining({ method: "POST" }),
      ),
    );
    const block = await screen.findByTestId("partner-chat-summary");
    expect(block.textContent).toContain("выжимка №1");
    expect(block.textContent).toContain("по состоянию на сообщение №12");

    // повторный клик — обновление: новый POST, текст замещается, блок один
    fireEvent.click(screen.getByRole("button", { name: "📄 Выжимка" }));
    await screen.findByText("выжимка №2");
    expect(document.querySelectorAll(".chat-summary")).toHaveLength(1);
    const posts = fetchMock.mock.calls.filter(
      ([u, i]) => String(u).endsWith("/t1/summary") && i?.method === "POST",
    );
    expect(posts).toHaveLength(2);
  });
});

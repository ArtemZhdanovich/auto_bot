/**
 * Тесты раздела «Поддержка» админ-панели (05-chats-marketplace.md §2/§6):
 * список тредов (карточки: юзер/сниппет/пауза, бейдж «ждут ответа»,
 * фильтр paused), выбор треда → комната оператора (лента через GET
 * messages, свои=operator справа), отправка ответа (POST messages) и
 * возврат к списку. HTTP-слой api.ts админа замокан целиком.
 *
 * 12-ai-trust §3: «📄 Выжимка» в шапке комнаты — POST …/summary →
 * раскрывающийся блок с текстом и «по состоянию на …»; повторный клик
 * обновляет выжимку.
 */

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AdminSupportThread } from "./api";

const mocks = vi.hoisted(() => ({
  fetchAdminChats: vi.fn(),
  fetchAdminUnreadChats: vi.fn(),
  fetchAdminMessages: vi.fn(),
  sendAdminMessage: vi.fn(),
  fetchAdminChatSummary: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./api")>()),
  fetchAdminChats: mocks.fetchAdminChats,
  fetchAdminUnreadChats: mocks.fetchAdminUnreadChats,
  fetchAdminMessages: mocks.fetchAdminMessages,
  sendAdminMessage: mocks.sendAdminMessage,
  fetchAdminChatSummary: mocks.fetchAdminChatSummary,
}));

import { Support } from "./AdminScreens";

const NOW = Date.parse("2026-10-25T12:00:00Z");

const THREADS: AdminSupportThread[] = [
  {
    id: "11111111-1111-1111-1111-111111111111",
    type: "support",
    status: "active",
    bot_paused: true,
    user_id: 910001,
    user_name: "anna",
    last_user_message_at: new Date(NOW - 5 * 60_000).toISOString(),
    last_user_message_snippet: "Помогите с доставкой в Казань",
  },
  {
    id: "22222222-2222-2222-2222-222222222222",
    type: "support",
    status: "active",
    bot_paused: false,
    user_id: 910002,
    user_name: null,
    last_user_message_at: null,
    last_user_message_snippet: null,
  },
];

const MSGS = [
  { id: 10, sender_type: "system", sender_id: null, body: "Задайте вопрос", created_at: new Date(NOW - 7 * 60_000).toISOString() },
  { id: 11, sender_type: "user", sender_id: 910001, body: "когда привезут?", created_at: new Date(NOW - 5 * 60_000).toISOString() },
  { id: 12, sender_type: "bot", sender_id: null, body: "25–40 **дней**", created_at: new Date(NOW - 4 * 60_000).toISOString() },
];

const onUnauthorized = vi.fn();

/** Хвост поллинга (after_id): непустой — его отдаёт второй и далее запрос. */
let pollTail: { id: number; sender_type: "user"; sender_id: number; body: string; created_at: string }[] = [];

beforeEach(() => {
  vi.clearAllMocks();
  pollTail = [];
  vi.useFakeTimers({ shouldAdvanceTime: true, now: NOW });
  vi.setSystemTime(NOW);
  mocks.fetchAdminChats.mockResolvedValue(THREADS);
  mocks.fetchAdminUnreadChats.mockResolvedValue(1);
  // Первая загрузка — полная лента; инкремент-поллинг — только хвост.
  mocks.fetchAdminMessages.mockImplementation(async (_id: string, opts?: { afterId?: number }) =>
    opts?.afterId != null && pollTail.length
      ? { items: pollTail, has_more: false }
      : { items: MSGS, has_more: false },
  );
  mocks.sendAdminMessage.mockImplementation(async (_id: string, body: string) => ({
    id: 13,
    sender_type: "operator",
    sender_id: 0,
    body,
    created_at: new Date(NOW).toISOString(),
  }));
  mocks.fetchAdminChatSummary.mockResolvedValue({
    summary: "Клиент спрашивает сроки доставки в Казань, торгуется по цене.",
    as_of_message_id: 12,
  });
});

afterEach(() => {
  vi.useRealTimers();
  cleanup();
});

const renderSupport = () => render(<Support onUnauthorized={onUnauthorized} />);

describe("раздел «Поддержка»: список тредов", () => {
  it("карточки: юзер, сниппет последнего юзер-сообщения, 🔕 пауза, бейдж «ждут ответа»", async () => {
    renderSupport();

    expect(await screen.findByRole("heading", { name: /Поддержка/ })).toBeTruthy();
    expect(await screen.findByText("anna")).toBeTruthy();
    expect(screen.getByText("Помогите с доставкой в Казань")).toBeTruthy();
    expect(screen.getByText("🔕 бот на паузе")).toBeTruthy();
    // клиент без имени — фолбэк id; без юзер-сообщений — заглушка
    expect(screen.getByText("Клиент #910002")).toBeTruthy();
    expect(screen.getByText("Сообщений от клиента ещё не было")).toBeTruthy();
    // unread-бейдж из GET /api/admin/chats/unread
    expect(screen.getByText("ждут ответа: 1")).toBeTruthy();
    // запрос по умолчанию: support без фильтра паузы
    expect(mocks.fetchAdminChats).toHaveBeenCalledWith({ type: "support", paused: undefined, limit: 50 });
  });

  it("фильтр «только с паузой» перезапрашивает список с paused=true", async () => {
    renderSupport();
    await screen.findByText("anna");

    fireEvent.click(screen.getByLabelText(/Только с паузой бота/));
    await waitFor(() =>
      expect(mocks.fetchAdminChats).toHaveBeenCalledWith({ type: "support", paused: true, limit: 50 }),
    );
  });

  it("поллинг списка каждые 30с (без сокета)", async () => {
    renderSupport();
    await screen.findByText("anna");
    const callsBefore = mocks.fetchAdminChats.mock.calls.length;
    await vi.advanceTimersByTimeAsync(30_000 + 100);
    await waitFor(() => expect(mocks.fetchAdminChats.mock.calls.length).toBeGreaterThan(callsBefore));
  });
});

describe("раздел «Поддержка»: комната оператора", () => {
  it("выбор треда → лента GET messages: свои=operator справа, клиент слева, system-строка", async () => {
    renderSupport();
    fireEvent.click(await screen.findByRole("button", { name: "Чат: anna" }));

    expect(await screen.findByTestId("admin-chat-room")).toBeTruthy();
    await waitFor(() =>
      expect(mocks.fetchAdminMessages).toHaveBeenCalledWith("11111111-1111-1111-1111-111111111111"),
    );
    await screen.findByText("когда привезут?");
    expect(document.querySelector('.ac-msg--other[data-id="11"]')).not.toBeNull();
    expect(document.querySelector('.ac-msg--system[data-id="10"]')).not.toBeNull();
    expect(document.querySelector('.ac-msg--centered[data-id="12"]')).not.toBeNull();
    // markdown-тело бота рендерится (bold → strong)
    expect(document.querySelector(".ac-msg__bubble strong")?.textContent).toBe("дней");
    // шапка: имя клиента + признак паузы
    expect(screen.getByText("Поддержка · 🔕 бот на паузе")).toBeTruthy();
  });

  it("композер: Enter отправляет POST messages, ответ падает справа (operator)", async () => {
    renderSupport();
    fireEvent.click(await screen.findByRole("button", { name: "Чат: anna" }));
    await screen.findByText("когда привезут?");

    const ta = screen.getByLabelText("Ответ оператора");
    fireEvent.change(ta, { target: { value: "На следующей неделе" } });
    fireEvent.keyDown(ta, { key: "Enter" });

    await waitFor(() =>
      expect(mocks.sendAdminMessage).toHaveBeenCalledWith(
        "11111111-1111-1111-1111-111111111111",
        "На следующей неделе",
      ),
    );
    await waitFor(() =>
      expect(document.querySelector('.ac-msg--own[data-id="13"]')).not.toBeNull(),
    );
    // поле очистилось после успешной отправки
    expect((screen.getByLabelText("Ответ оператора") as HTMLTextAreaElement).value).toBe("");
  });

  it("поллинг комнаты 10с: подтягивает только хвост (after_id последнего id)", async () => {
    renderSupport();
    fireEvent.click(await screen.findByRole("button", { name: "Чат: anna" }));
    await screen.findByText("когда привезут?");

    // следующий тик поллинга вернёт новое сообщение юзера
    pollTail = [
      { id: 14, sender_type: "user", sender_id: 910001, body: "а точнее?", created_at: new Date(NOW).toISOString() },
    ];

    await vi.advanceTimersByTimeAsync(10_000 + 100);
    await screen.findByText("а точнее?");
    // инкремент шёл после последнего id первой загрузки (12)
    await waitFor(() =>
      expect(mocks.fetchAdminMessages).toHaveBeenLastCalledWith(
        "11111111-1111-1111-1111-111111111111",
        { afterId: 12 },
      ),
    );
  });

  it("назад — к списку, список перезагружается", async () => {
    renderSupport();
    fireEvent.click(await screen.findByRole("button", { name: "Чат: anna" }));
    await screen.findByText("когда привезут?");
    const calls = mocks.fetchAdminChats.mock.calls.length;

    fireEvent.click(screen.getByRole("button", { name: "К списку тредов" }));
    await waitFor(() =>
      expect(mocks.fetchAdminChats.mock.calls.length).toBeGreaterThan(calls),
    );
    expect(screen.queryByTestId("admin-chat-room")).toBeNull();
    expect(screen.getByText("anna")).toBeTruthy();
  });
});

// ---------- «📄 Выжимка» диалога (12-ai-trust §3) ----------

describe("Комната оператора: «📄 Выжимка» (12 §3)", () => {
  it("клик → POST …/summary → блок с текстом и «по состоянию на …»", async () => {
    renderSupport();
    fireEvent.click(await screen.findByRole("button", { name: "Чат: anna" }));
    await screen.findByText("когда привезут?");
    // до клика блока выжимки нет
    expect(screen.queryByTestId("admin-chat-summary")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "📄 Выжимка" }));
    await waitFor(() =>
      expect(mocks.fetchAdminChatSummary).toHaveBeenCalledWith(
        "11111111-1111-1111-1111-111111111111",
      ),
    );
    const block = await screen.findByTestId("admin-chat-summary");
    expect(block.textContent).toContain("Клиент спрашивает сроки доставки в Казань");
    expect(block.textContent).toContain("по состоянию на сообщение №12");
  });

  it("повторный клик обновляет выжимку (новый POST, текст замещается)", async () => {
    let n = 0;
    mocks.fetchAdminChatSummary.mockImplementation(async () => ({
      summary: `выжимка №${++n}`,
      as_of_message_id: 12,
    }));
    renderSupport();
    fireEvent.click(await screen.findByRole("button", { name: "Чат: anna" }));
    await screen.findByText("когда привезут?");

    fireEvent.click(screen.getByRole("button", { name: "📄 Выжимка" }));
    expect(await screen.findByText("выжимка №1")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "📄 Выжимка" }));
    expect(await screen.findByText("выжимка №2")).toBeTruthy();
    // блок один — старого текста нет
    expect(document.querySelectorAll(".ac-summary")).toHaveLength(1);
    expect(mocks.fetchAdminChatSummary).toHaveBeenCalledTimes(2);
  });
});

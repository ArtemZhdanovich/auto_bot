/**
 * Тесты транспорта чатов (lib/chatSocket):
 *  - thread-режим (партнёрский кабинет): поллинг-фоллбэк (fetcher
 *    подменён DI), курсор after_id, интерфейс событий, выбор транспорта;
 *  - личный одноканальный режим (09 §1): createChatTransport
 *    {userChannel} — фоллбэк UserChannelPollingTransport дергает список
 *    (onThreads) + after_id ОТКРЫТОГО треда (setActiveThread), каналы
 *    thread:* не обслуживает;
 *  - normalizeChatEvent: плоские payload с thread_id + lot_expired.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  createChatTransport,
  normalizeChatEvent,
  PollingTransport,
  threadChannel,
  userChannel,
  UserChannelPollingTransport,
  type ChatEvent,
  type ChatMessage,
} from "./chatSocket";

const msg = (id: number, body = `m${id}`): ChatMessage => ({
  id,
  sender_type: "bot",
  sender_id: null,
  body,
  created_at: "2026-10-25T10:00:00Z",
});

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("PollingTransport (фоллбэк ws → REST ?after_id=)", () => {
  it("опрашивает каждые 5с, двигает after_id и отдаёт message-события", async () => {
    const pages = [
      { items: [msg(1), msg(3)], has_more: true },
      { items: [msg(5)], has_more: false },
      { items: [] as ChatMessage[], has_more: false },
    ];
    const calls: Array<[string, number]> = [];
    const fetcher = vi.fn(async (threadId: string, afterId: number) => {
      calls.push([threadId, afterId]);
      return pages[Math.min(calls.length - 1, pages.length - 1)];
    });

    const t = new PollingTransport("uuid-1", { intervalMs: 5000, fetcher });
    const events: ChatEvent[] = [];
    t.onEvent((ev) => events.push(ev));

    t.subscribe(threadChannel("uuid-1")); // канал треда — поллинг активен
    await t.connect(); // первый тик сразу
    expect(calls[0]).toEqual(["uuid-1", 0]);
    expect(events.map((e) => e.type)).toEqual(["message", "message"]);

    await vi.advanceTimersByTimeAsync(5000);
    // after_id продвинулся до максимального id первой страницы (3)
    expect(calls[1]).toEqual(["uuid-1", 3]);
    expect(events.length).toBe(3);

    await vi.advanceTimersByTimeAsync(5000);
    expect(calls[2]).toEqual(["uuid-1", 5]); // и до 5 после второй
    expect(events.length).toBe(3); // пустая страница — без событий
  });

  it("порядок внутри страницы не важен: сортируем по id и не дублируем", async () => {
    const fetcher = vi.fn(async () => ({
      items: [msg(7), msg(2), msg(2)], // 2 повторно — дубликат отдаляется
      has_more: false,
    }));
    const t = new PollingTransport("u", { intervalMs: 1000, fetcher });
    const seen: number[] = [];
    t.onEvent((e) => e.type === "message" && seen.push(e.message.id));
    t.subscribe(threadChannel("u"));
    await t.connect();
    expect(seen).toEqual([2, 7]);
  });

  it("onEvent возвращает отписку: после off() события не приходят", async () => {
    const fetcher = vi.fn(async () => ({ items: [msg(1)], has_more: false }));
    const t = new PollingTransport("u", { intervalMs: 1000, fetcher });
    const seen: number[] = [];
    const off = t.onEvent((e) => e.type === "message" && seen.push(e.message.id));
    t.subscribe(threadChannel("u"));
    await t.connect();
    off();
    await vi.advanceTimersByTimeAsync(3000);
    expect(seen).toEqual([1]);
  });

  it("unsubscribe канала треда и disconnect останавливают опрос", async () => {
    const fetcher = vi.fn(async () => ({ items: [] as ChatMessage[], has_more: false }));
    const t = new PollingTransport("u", { intervalMs: 1000, fetcher });
    t.subscribe(threadChannel("u"));
    await t.connect();
    expect(fetcher).toHaveBeenCalledTimes(1);

    t.unsubscribe(threadChannel("u"));
    await vi.advanceTimersByTimeAsync(2500);
    expect(fetcher).toHaveBeenCalledTimes(1); // тишина

    t.subscribe(threadChannel("u"));
    t.disconnect();
    await vi.advanceTimersByTimeAsync(2500);
    expect(fetcher).toHaveBeenCalledTimes(1); // интервал снят
  });

  it("чужие каналы (user:*) поллинг не обслуживает", async () => {
    const fetcher = vi.fn(async () => ({ items: [] as ChatMessage[], has_more: false }));
    const t = new PollingTransport("u", { intervalMs: 1000, fetcher });
    t.subscribe(userChannel(42));
    await t.connect();
    await vi.advanceTimersByTimeAsync(1500);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("ошибка сети молчит: событие не теряется на следующем тике", async () => {
    let fail = true;
    const fetcher = vi.fn(async () => {
      if (fail) throw new Error("network");
      return { items: [msg(1)], has_more: false };
    });
    const t = new PollingTransport("u", { intervalMs: 1000, fetcher });
    const events: ChatEvent[] = [];
    t.onEvent((ev) => events.push(ev));
    t.subscribe(threadChannel("u"));
    await t.connect();
    expect(events).toEqual([]); // ошибка проглочена
    fail = false;
    await vi.advanceTimersByTimeAsync(1000);
    expect(events.map((e) => e.type)).toEqual(["message"]);
  });
});

describe("createChatTransport: выбор ws/поллинг", () => {
  it("ws-token недоступен → фоллбэк на PollingTransport", async () => {
    const t = await createChatTransport("uuid", {
      tokenFetcher: async () => {
        throw new Error("401");
      },
      polling: { intervalMs: 60000, fetcher: async () => ({ items: [], has_more: false }) },
    });
    expect(t.kind).toBe("polling");
    t.disconnect();
  });

  it("forcePolling=true → сразу поллинг, без ws-token", async () => {
    const tokenFetcher = vi.fn(async () => ({ token: "t", url: "ws://x" }));
    const t = await createChatTransport("uuid", {
      forcePolling: true,
      tokenFetcher,
      polling: { intervalMs: 60000, fetcher: async () => ({ items: [], has_more: false }) },
    });
    expect(t.kind).toBe("polling");
    expect(tokenFetcher).not.toHaveBeenCalled();
    t.disconnect();
  });
});

describe("Личный одноканальный режим (09 §1): createChatTransport({userChannel})", () => {
  const noPolling = {
    intervalMs: 60000,
    listFetcher: async () => [],
    messagesFetcher: async () => ({ items: [] as ChatMessage[], has_more: false }),
  };

  it("ws-token недоступен → фоллбэк-поллинг списка + after_id открытого треда", async () => {
    const listFetcher = vi.fn(async () => [{ id: "t1" }]);
    const messagesFetcher = vi.fn(async (_t: string, afterId: number) => ({
      items: afterId === 0 ? [msg(1), msg(2)] : [],
      has_more: false,
    }));
    const t = await createChatTransport(
      { userChannel: userChannel(42) },
      {
        tokenFetcher: async () => {
          throw new Error("401");
        },
        polling: { intervalMs: 5000, listFetcher, messagesFetcher },
      },
    );
    expect(t.kind).toBe("polling");
    t.subscribe(userChannel(42));

    const threads: unknown[] = [];
    const events: ChatEvent[] = [];
    t.onThreads((raw) => threads.push(raw));
    t.onEvent((ev) => events.push(ev));
    // комнаты нет — дергаем только список
    await t.connect();
    expect(listFetcher).toHaveBeenCalledTimes(1);
    expect(messagesFetcher).not.toHaveBeenCalled();
    expect(threads).toHaveLength(1);

    // открыли комнату → тот же тик (интервал 5с) докачивает after_id
    t.setActiveThread("t1", 0);
    await vi.advanceTimersByTimeAsync(5000);
    expect(messagesFetcher).toHaveBeenCalledWith("t1", 0);
    expect(events.map((e) => e.type)).toEqual(["message", "message"]);
    expect(events[0]).toMatchObject({ type: "message", thread_id: "t1" });

    // курсор двинулся: следующий тик спрашивает после 2
    await vi.advanceTimersByTimeAsync(5000);
    expect(messagesFetcher).toHaveBeenLastCalledWith("t1", 2);
    t.disconnect();
  });

  it("forcePolling → сразу поллинг, ws-token не спрашиваем", async () => {
    const tokenFetcher = vi.fn(async () => ({ token: "t", url: "ws://x" }));
    const t = await createChatTransport(
      { userChannel: userChannel(7) },
      { forcePolling: true, tokenFetcher, polling: noPolling },
    );
    expect(t.kind).toBe("polling");
    expect(tokenFetcher).not.toHaveBeenCalled();
    t.disconnect();
  });

  it("subscribe/unsubscribe только личного канала: thread:* поллинг не активирует", async () => {
    const listFetcher = vi.fn(async () => []);
    const t = new UserChannelPollingTransport(userChannel(42), {
      intervalMs: 1000,
      listFetcher,
      messagesFetcher: async () => ({ items: [msg(1)], has_more: false }),
    });
    t.subscribe(threadChannel("uuid-1")); // чужой канал — игнор
    await t.connect();
    await vi.advanceTimersByTimeAsync(1500);
    expect(listFetcher).not.toHaveBeenCalled();

    t.subscribe(userChannel(42)); // свой канал — поллинг жив
    await vi.advanceTimersByTimeAsync(1000);
    expect(listFetcher).toHaveBeenCalled();
    t.disconnect();
  });

  it("onEvent/onThreads возвращают отписку", async () => {
    const t = new UserChannelPollingTransport(userChannel(1), {
      intervalMs: 1000,
      listFetcher: async () => [],
      messagesFetcher: async () => ({ items: [msg(1)], has_more: false }),
    });
    const seen: string[] = [];
    const offEv = t.onEvent((e) => seen.push(e.type));
    const offTh = t.onThreads(() => seen.push("threads"));
    t.subscribe(userChannel(1));
    t.setActiveThread("t1", 0);
    await t.connect();
    expect(seen.length).toBeGreaterThan(0);
    const before = seen.length;
    offEv();
    offTh();
    await vi.advanceTimersByTimeAsync(1200);
    expect(seen.length).toBe(before); // тишина после отписки
    t.disconnect();
  });

  it("ошибка списка/сообщений молчит: тик не умирает", async () => {
    let fail = true;
    const t = new UserChannelPollingTransport(userChannel(1), {
      intervalMs: 1000,
      listFetcher: async () => {
        if (fail) throw new Error("network");
        return [];
      },
      messagesFetcher: async () => ({ items: [msg(1)], has_more: false }),
    });
    const events: ChatEvent[] = [];
    t.onEvent((ev) => events.push(ev));
    t.subscribe(userChannel(1));
    t.setActiveThread("t1", 0);
    await t.connect();
    expect(events).toEqual([]); // ошибка списка проглочена, тик жил дальше
    fail = false;
    await vi.advanceTimersByTimeAsync(1000);
    expect(events.map((e) => e.type)).toEqual(["message"]);
    t.disconnect();
  });
});

describe("normalizeChatEvent: плоский payload бэка (use_cases.message_event)", () => {
  it("message — плоская форма разворачивается в {type,message} + thread_id", () => {
    const ev = normalizeChatEvent({
      type: "message",
      thread_id: "t1",
      id: 5,
      sender_type: "partner",
      sender_id: 7,
      body: "в наличии",
      created_at: "2026-10-25T10:00:00Z",
    });
    expect(ev).toEqual({
      type: "message",
      thread_id: "t1",
      message: {
        id: 5,
        sender_type: "partner",
        sender_id: 7,
        body: "в наличии",
        created_at: "2026-10-25T10:00:00Z",
      },
    });
  });

  it("message — вложенная форма проходит насквозь (pass-through)", () => {
    const ev = normalizeChatEvent({ type: "message", message: msg(9) });
    expect(ev).toEqual({ type: "message", message: msg(9) });
  });

  it("read/typing — поля совпадают, thread_id проходим; мусор — null", () => {
    expect(
      normalizeChatEvent({ type: "read", thread_id: "t1", user_type: "partner", last_read_message_id: 4 }),
    ).toEqual({ type: "read", thread_id: "t1", user_type: "partner", last_read_message_id: 4 });
    expect(normalizeChatEvent({ type: "typing", thread_id: "t1", user_type: "bot" })).toEqual({
      type: "typing",
      thread_id: "t1",
      user_type: "bot",
    });
    expect(normalizeChatEvent({ nope: 1 })).toBeNull();
    expect(normalizeChatEvent({ type: "message", message: "не то" })).toBeNull();
  });

  it("lot_expired (09 §1) — проход; без thread_id — мусор", () => {
    expect(normalizeChatEvent({ type: "lot_expired", thread_id: "t1", listing_id: 7 })).toEqual({
      type: "lot_expired",
      thread_id: "t1",
      listing_id: 7,
    });
    expect(normalizeChatEvent({ type: "lot_expired", listing_id: 7 })).toBeNull();
  });

  it("listing_status (10 §3): {listing_id, status, reason?}; без полей — мусор", () => {
    expect(normalizeChatEvent({ type: "listing_status", listing_id: 5, status: "accepted" })).toEqual({
      type: "listing_status",
      listing_id: 5,
      status: "accepted",
    });
    expect(
      normalizeChatEvent({ type: "listing_status", listing_id: 5, status: "rejected", reason: "спам" }),
    ).toEqual({ type: "listing_status", listing_id: 5, status: "rejected", reason: "спам" });
    // без listing_id / статуса — событие не маршрутизируется
    expect(normalizeChatEvent({ type: "listing_status", status: "accepted" })).toBeNull();
    expect(normalizeChatEvent({ type: "listing_status", listing_id: 5 })).toBeNull();
  });
});

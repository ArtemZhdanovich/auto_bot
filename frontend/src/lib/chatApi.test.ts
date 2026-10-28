/**
 * Тесты нормализации контракта чатов (lib/chatApi): бэк отдаёт плоский
 * list[MessageOut] и ThreadOut с subject.id / last_message.sender
 * (routers/chats.py) — ядро F1 работает с {items,has_more} и
 * subject.listing_id / last_message.sender_type.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock("../api/client", () => ({ api: mocks.api }));

import {
  archiveChat,
  createChatInvite,
  createGroupChat,
  fetchChats,
  fetchMessages,
  isGroupFullError,
  isOwnerMustArchiveError,
  joinGroupChat,
  kickChatParticipant,
  leaveChat,
  newIdempotencyKey,
  reportChat,
  sendMessage,
  setChatMuted,
  toChatThread,
} from "./chatApi";

afterEach(() => {
  vi.clearAllMocks();
});

describe("toChatThread: сырой ThreadOut бэка", () => {
  it("subject.id → listing_id, last_message.sender → sender_type", () => {
    const t = toChatThread({
      id: "uuid-1",
      type: "seller",
      title: "Продавец · Kia K5",
      status: "active",
      unread: 3,
      last_message: {
        body: "Ещё в продаже",
        created_at: "2026-10-25T10:00:00Z",
        sender: "partner",
        sender_id: 7,
      },
      subject: { id: 42, make: "Kia", model: "K5", year: 2022, price: 1000, currency: "KRW", url: "u", image: null },
    });
    expect(t.subject?.listing_id).toBe(42);
    expect(t.last_message?.sender_type).toBe("partner");
    expect(t.type).toBe("seller");
  });

  it("уже-нормализованная форма проходит насквозь", () => {
    const t = toChatThread({
      id: "u2",
      type: "support",
      title: "Поддержка",
      status: "active",
      unread: 0,
      last_message: null,
      subject: { listing_id: 9, make: null, model: null, year: null, price: null, currency: null, image: null },
    });
    expect(t.subject?.listing_id).toBe(9);
    expect(t.type).toBe("support");
    expect(t.is_group).toBe(false);
  });

  it("group-тред (07 §2): is_group, participants, title", () => {
    const t = toChatThread({
      id: "g1",
      type: "group",
      title: "Осмотр Kia K5",
      status: "active",
      unread: 1,
      last_message: null,
      subject: null,
      is_group: true,
      participants: [
        { user_type: "user", user_id: 42, name: "Аня" },
        { user_type: "partner", user_id: 7, name: "Партнёр Сеул" },
      ],
    });
    expect(t.is_group).toBe(true);
    expect(t.type).toBe("group");
    expect(t.title).toBe("Осмотр Kia K5");
    expect(t.participants).toEqual([
      { user_type: "user", user_id: 42, name: "Аня", role: "member" },
      { user_type: "partner", user_id: 7, name: "Партнёр Сеул", role: "member" },
    ]);
  });

  it("type=group распознаётся и без явного is_group", () => {
    const t = toChatThread({ id: "g2", type: "group", title: "T", status: "active", unread: 0, last_message: null, subject: null });
    expect(t.is_group).toBe(true);
  });

  it("participants чистятся от мусора (id=0 выбрасывается)", () => {
    const t = toChatThread({
      id: "g3",
      type: "group",
      title: "T",
      status: "active",
      unread: 0,
      last_message: null,
      subject: null,
      participants: [
        { user_type: "user", user_id: 0, name: "" },
        { user_type: "stranger", user_id: 5, name: "X" },
      ],
    });
    expect(t.participants).toEqual([{ user_type: "user", user_id: 5, name: "X", role: "member" }]);
  });
});

describe("fetchChats: список через нормализатор", () => {
  it("мапит сырой массив", async () => {
    mocks.api.mockResolvedValue([
      { id: "a", type: "support", title: "T", status: "active", unread: 1, last_message: null, subject: null },
    ]);
    const list = await fetchChats();
    expect(list).toHaveLength(1);
    expect(list[0].type).toBe("support");
  });
});

describe("fetchMessages: плоский список и конверт", () => {
  const m = (id: number) => ({
    id,
    sender_type: "user",
    sender_id: 1,
    body: `m${id}`,
    created_at: "2026-10-25T10:00:00Z",
  });

  it("плоский list[MessageOut] → {items, has_more}", async () => {
    mocks.api.mockResolvedValue([m(1), m(2)]);
    const page = await fetchMessages("t1");
    expect(page.items.map((x) => x.id)).toEqual([1, 2]);
    expect(page.has_more).toBe(false);
  });

  it("полная страница (limit) — has_more=true", async () => {
    mocks.api.mockResolvedValue(Array.from({ length: 3 }, (_, i) => m(i + 1)));
    const page = await fetchMessages("t1", { limit: 3 });
    expect(page.has_more).toBe(true);
  });

  it("конверт {items,has_more} — pass-through", async () => {
    mocks.api.mockResolvedValue({ items: [m(5)], has_more: true });
    const page = await fetchMessages("t1");
    expect(page.items[0].id).toBe(5);
    expect(page.has_more).toBe(true);
  });
});

describe("sendMessage: Idempotency-Key (долг ревизора №7)", () => {
  it("каждая отправка несёт заголовок с уникальным uuid", async () => {
    mocks.api.mockResolvedValue({ id: 1, sender_type: "user", sender_id: 1, body: "привет", created_at: "2026-10-25T10:00:00Z" });
    await sendMessage("t1", "привет");
    await sendMessage("t1", "ещё");

    const keys = mocks.api.mock.calls.map(
      ([, init]) => ((init as RequestInit).headers as Record<string, string>)?.["Idempotency-Key"],
    );
    expect(keys).toHaveLength(2);
    expect(keys[0]).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(keys[0]).not.toBe(keys[1]);
  });

  it("newIdempotencyKey: fallback без crypto.randomUUID тоже уникален", () => {
    const real = globalThis.crypto;
    // временно прячем randomUUID — остаётся fallback-ветка
    Object.defineProperty(globalThis, "crypto", {
      value: { ...real, randomUUID: undefined },
      configurable: true,
    });
    try {
      const a = newIdempotencyKey();
      const b = newIdempotencyKey();
      expect(a).toMatch(/^idem-/);
      expect(a).not.toBe(b);
    } finally {
      Object.defineProperty(globalThis, "crypto", { value: real, configurable: true });
    }
  });
});

describe("переговорные (07 §2): REST-контракты", () => {
  it("createGroupChat: POST /chats/group {listing_id!, title?} (08 §1)", async () => {
    mocks.api.mockResolvedValue({ thread_id: "g1" });
    await createGroupChat(7, "Осмотр Kia K5");
    const [path, init] = mocks.api.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/chats/group");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ listing_id: 7, title: "Осмотр Kia K5" });

    // пустой title не уходит — бэк автогенерит «Переговоры: {Make Model Year}»
    await createGroupChat(9);
    const [, init2] = mocks.api.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(init2.body))).toEqual({ listing_id: 9 });
  });

  it("createChatInvite: POST /chats/{id}/invite → код", async () => {
    mocks.api.mockResolvedValue({ invite_code: "AB12CD" });
    const code = await createChatInvite("g1");
    expect(code).toBe("AB12CD");
    const [path, init] = mocks.api.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/chats/g1/invite");
    expect(init.method).toBe("POST");
  });

  it("joinGroupChat: POST /chats/join {code}", async () => {
    mocks.api.mockResolvedValue({ thread_id: "j1" });
    const id = await joinGroupChat("ab12cd");
    expect(id).toBe("j1");
    const [path, init] = mocks.api.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/chats/join");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ code: "ab12cd" });
  });

  it("isGroupFullError: 409 detail с group_is_full распознаётся по подстроке", () => {
    expect(isGroupFullError(new Error("group_is_full"))).toBe(true);
    expect(isGroupFullError(new Error("Group is full: group_is_full"))).toBe(true);
    expect(isGroupFullError(new Error("invite code expired"))).toBe(false);
    expect(isGroupFullError("не ошибка")).toBe(false);
  });
});

describe("говернанс переговорных (08 §2/§4/§6): REST-контракты", () => {
  it("toChatThread: role/muted треда и role участников", () => {
    const t = toChatThread({
      id: "g9",
      type: "group",
      title: "Осмотр",
      status: "active",
      unread: 2,
      last_message: null,
      subject: null,
      is_group: true,
      role: "owner",
      muted: true,
      participants: [
        { user_type: "user", user_id: 42, name: "Аня", role: "owner" },
        { user_type: "partner", user_id: 7, name: "Партнёр" },
      ],
    });
    expect(t.role).toBe("owner");
    expect(t.muted).toBe(true);
    expect(t.participants?.[0].role).toBe("owner");
    // без поля role — member (старые фейки)
    expect(t.participants?.[1].role).toBe("member");
    // тред без role/muted — дефолты
    const plain = toChatThread({ id: "s1", type: "seller", title: "T", status: "active", unread: 0, last_message: null, subject: null });
    expect(plain.role).toBe("member");
    expect(plain.muted).toBe(false);
  });

  it("setChatMuted: POST /mute|/unmute → итоговый muted (или запрошенный)", async () => {
    mocks.api.mockResolvedValue({ muted: true });
    await expect(setChatMuted("g1", true)).resolves.toBe(true);
    let [path, init] = mocks.api.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/chats/g1/mute");
    expect(init.method).toBe("POST");

    mocks.api.mockResolvedValue({}); // старый бэк без поля — берём запрошенное
    await expect(setChatMuted("g1", false)).resolves.toBe(false);
    [path, init] = mocks.api.mock.calls[1] as [string, RequestInit];
    expect(path).toBe("/chats/g1/unmute");
  });

  it("leaveChat/archiveChat: POST без тела", async () => {
    mocks.api.mockResolvedValue(undefined);
    await leaveChat("g1");
    await archiveChat("g1");
    const [p1, i1] = mocks.api.mock.calls[0] as [string, RequestInit];
    const [p2, i2] = mocks.api.mock.calls[1] as [string, RequestInit];
    expect(p1).toBe("/chats/g1/leave");
    expect(i1.method).toBe("POST");
    expect(p2).toBe("/chats/g1/archive");
    expect(i2.method).toBe("POST");
  });

  it("kickChatParticipant: DELETE /chats/{id}/participants/{type}/{id}", async () => {
    mocks.api.mockResolvedValue(undefined);
    await kickChatParticipant("g1", "partner", 7);
    const [path, init] = mocks.api.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/chats/g1/participants/partner/7");
    expect(init.method).toBe("DELETE");
  });

  it("reportChat: POST /report {reason, comment?} — 201/200 идемпотентно", async () => {
    mocks.api.mockResolvedValue(undefined);
    await reportChat("g1", "spam");
    await reportChat("g1", "fraud", "просит предоплату вне площадки");
    const [p1, i1] = mocks.api.mock.calls[0] as [string, RequestInit];
    const [p2, i2] = mocks.api.mock.calls[1] as [string, RequestInit];
    expect(p1).toBe("/chats/g1/report");
    expect(i1.method).toBe("POST");
    expect(JSON.parse(String(i1.body))).toEqual({ reason: "spam" });
    expect(JSON.parse(String(i2.body))).toEqual({
      reason: "fraud",
      comment: "просит предоплату вне площадки",
    });
  });

  it("isOwnerMustArchiveError: 409 owner_must_archive по подстроке", () => {
    expect(isOwnerMustArchiveError(new Error("owner_must_archive"))).toBe(true);
    expect(isOwnerMustArchiveError(new Error("Cannot leave: owner_must_archive"))).toBe(true);
    expect(isOwnerMustArchiveError(new Error("group_is_full"))).toBe(false);
  });
});

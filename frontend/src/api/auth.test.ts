/** Юнит-тесты auth-слоя: Telegram-контекст, гостевой режим, веб-вход. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  authorize,
  clearGuest,
  guestLogin,
  isGuest,
  isTelegramContext,
  webLogin,
  webRegister,
} from "./auth";


function mockStorage(): Storage {
  const map = new Map<string, string>();
  return {
    getItem: (k: string) => map.get(k) ?? null,
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
    clear: () => void map.clear(),
    key: () => null,
    get length() {
      return map.size;
    },
  } as Storage;
}

const fetchMock = vi.fn();

function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    statusText: "Err",
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const AUTH = {
  access_token: "a1",
  refresh_token: "r1",
  token_type: "bearer",
  user: { telegram_id: -1, first_name: "Гость", subscription_tier: "free", role: "buyer", is_active: true, created_at: "2026-01-01" },
};

beforeEach(() => {
  vi.stubGlobal("sessionStorage", mockStorage());
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("Telegram-контекст", () => {
  it("без window.Telegram — не Telegram", () => {
    vi.stubGlobal("window", {});
    expect(isTelegramContext()).toBe(false);
    expect(authorize()).resolves.toBeNull();
  });

  it("initData пуст → authorize null и без запросов", async () => {
    vi.stubGlobal("window", { Telegram: { WebApp: { initData: "" } } });
    await expect(authorize()).resolves.toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("гостевой режим", () => {
  it("guestLogin: POST /auth/guest, токены + флаг гостя", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, AUTH));
    const resp = await guestLogin();
    expect(resp.access_token).toBe("a1");
    expect(isGuest()).toBe(true);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/auth/guest");
    expect(init.method).toBe("POST");
  });

  it("clearGuest снимает флаг", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, AUTH));
    await guestLogin();
    clearGuest();
    expect(isGuest()).toBe(false);
  });

  it("ошибка сети — guestLogin кидает, флаг не ставится", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("down"));
    await expect(guestLogin()).rejects.toThrow("down");
    expect(isGuest()).toBe(false);
  });
});

describe("веб-вход", () => {
  it("webLogin шлёт email+password и сохраняет токены", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, AUTH));
    await webLogin("a@b.ru", "pw");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/auth/login");
    expect(JSON.parse(init.body as string)).toEqual({ email: "a@b.ru", password: "pw" });
  });

  it("webRegister шлёт first_name, сохраняет токены", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, AUTH));
    await webRegister("c@d.ru", "pw", "Иван");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/auth/register");
    expect(JSON.parse(init.body as string)).toEqual({ email: "c@d.ru", password: "pw", first_name: "Иван" });
  });

  it("409 при занятом email — ошибка с detail", async () => {
    fetchMock.mockResolvedValueOnce({
      status: 409,
      statusText: "Conflict",
      ok: false,
      json: () => Promise.resolve({ detail: "Email already registered" }),
    } as unknown as Response);
    await expect(webRegister("a@b.ru", "pw")).rejects.toThrow("Email already registered");
  });
});

/** Юнит-тесты auth-слоя: Telegram-контекст, гостевой режим, веб-вход,
 *  персистентная сессия и авто-refresh токенов. */
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
import {
  api,
  getAccessToken,
  getRefreshToken,
  setSessionExpiredHandler,
  setTokens,
} from "./client";

/** Токены и гостевой флаг хранятся в localStorage (lib/persistentState);
 *  jsdom даёт настоящий localStorage — чистим его перед каждым тестом. */

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
  localStorage.clear();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
  setSessionExpiredHandler(null);
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

describe("персистентная сессия (localStorage, переживает F5)", () => {
  it("токены и гостевой флаг читаются после ре-инициализации модуля", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, AUTH));
    await guestLogin();
    // «перезагрузка страницы»: модульная карта чистится, но localStorage — тот же
    vi.resetModules();
    const { getAccessToken: getAccess2, getRefreshToken: getRefresh2 } = await import("./client");
    expect(getAccess2()).toBe("a1");
    expect(getRefresh2()).toBe("r1");
    const { isGuest: isGuest2 } = await import("./auth");
    expect(isGuest2()).toBe(true);
  });

  it("logout стирает и токены, и гостевой флаг", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(201, AUTH));
    await guestLogin();
    const { logout } = await import("./auth");
    logout();
    expect(getAccessToken()).toBeNull();
    expect(getRefreshToken()).toBeNull();
    expect(isGuest()).toBe(false);
  });
});

describe("авто-refresh при 401", () => {
  it("401 → POST /auth/refresh → повтор запроса с новым токеном", async () => {
    setTokens("old", "r1");
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(jsonResponse(200, { access_token: "a2", refresh_token: "r2" }))
      .mockResolvedValueOnce(jsonResponse(200, { ok: 1 }));
    const data = await api<{ ok: number }>("/me");
    expect(data).toEqual({ ok: 1 });
    expect(fetchMock.mock.calls[1][0]).toBe("/api/auth/refresh");
    const refreshInit = fetchMock.mock.calls[1][1] as RequestInit;
    expect(JSON.parse(refreshInit.body as string)).toEqual({ refresh_token: "r1" });
    const retryInit = fetchMock.mock.calls[2][1] as RequestInit;
    expect((retryInit.headers as Record<string, string>).Authorization).toBe("Bearer a2");
    expect(getAccessToken()).toBe("a2"); // новая пара сохранена
  });

  it("refresh не прошёл → исходная 401 наверх + тихий гостевой fallback, токены не стираем", async () => {
    setTokens("old", "dead");
    const onExpired = vi.fn().mockResolvedValue(undefined);
    setSessionExpiredHandler(onExpired);
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: "bad refresh" }));
    await expect(api("/me")).rejects.toThrow("expired");
    expect(onExpired).toHaveBeenCalledOnce();
    expect(getAccessToken()).toBe("old"); // «Выход» — единственный способ стереть
  });

  it("без refresh-токена — сразу fallback, без запроса /auth/refresh", async () => {
    const onExpired = vi.fn();
    setSessionExpiredHandler(onExpired);
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }));
    await expect(api("/me")).rejects.toThrow("expired");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(onExpired).toHaveBeenCalledOnce();
  });

  it("параллельные 401 ждут один refresh (промис-синглтон)", async () => {
    setTokens("old", "r1");
    fetchMock.mockImplementation(async (url: string, init?: RequestInit) => {
      if (url === "/api/auth/refresh") {
        return jsonResponse(200, { access_token: "a2", refresh_token: "r2" });
      }
      const auth = (init?.headers as Record<string, string>)?.Authorization;
      return auth === "Bearer old"
        ? jsonResponse(401, { detail: "expired" })
        : jsonResponse(200, { ok: 1 });
    });
    const [a, b] = await Promise.all([api("/a"), api("/b")]);
    expect(a).toEqual({ ok: 1 });
    expect(b).toEqual({ ok: 1 });
    const refreshCalls = fetchMock.mock.calls.filter(([u]) => u === "/api/auth/refresh");
    expect(refreshCalls).toHaveLength(1);
  });

  it("401 на /auth/* не запускает авто-восстановление (учётные данные неверны)", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "Invalid credentials" }));
    await expect(api("/auth/login", { method: "POST", body: "{}" })).rejects.toThrow(
      "Invalid credentials",
    );
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("после 401 от повтора — ошибка наверх без повторного refresh", async () => {
    setTokens("old", "r1");
    fetchMock
      .mockResolvedValueOnce(jsonResponse(401, { detail: "expired" }))
      .mockResolvedValueOnce(jsonResponse(200, { access_token: "a2", refresh_token: "r2" }))
      .mockResolvedValueOnce(jsonResponse(401, { detail: "still bad" }));
    await expect(api("/me")).rejects.toThrow("still bad");
    expect(fetchMock).toHaveBeenCalledTimes(3); // исходный + refresh + один повтор
  });
});

/** Юнит-тесты HTTP-клиента: токены, Authorization, ошибки (vitest, node). */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, api, clearTokens, getAccessToken, setTokens } from "./client";

/** Минимальная заглушка sessionStorage (в node-окружении её нет). */
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

function jsonResponse(status: number, body: unknown): Response {
  return {
    status,
    statusText: status === 401 ? "Unauthorized" : "Error",
    ok: status >= 200 && status < 300,
    json: () => Promise.resolve(body),
  } as unknown as Response;
}

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("sessionStorage", mockStorage());
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("token storage", () => {
  it("setTokens/getAccessToken/clearTokens roundtrip", () => {
    expect(getAccessToken()).toBeNull();
    setTokens("a1", "r1");
    expect(getAccessToken()).toBe("a1");
    clearTokens();
    expect(getAccessToken()).toBeNull();
  });
});

describe("api()", () => {
  it("GET без токена — без Authorization", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(200, { ok: 1 }));
    const data = await api<{ ok: number }>("/listings");
    expect(data).toEqual({ ok: 1 });
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
    expect(fetchMock.mock.calls[0][0]).toBe("/api/listings");
  });

  it("GET с токеном — Bearer в заголовке", async () => {
    setTokens("jwt-abc", "r");
    fetchMock.mockResolvedValueOnce(jsonResponse(200, {}));
    await api("/me");
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer jwt-abc");
  });

  it("204 → undefined (не парсит тело)", async () => {
    fetchMock.mockResolvedValueOnce({ status: 204, ok: true } as unknown as Response);
    await expect(api("/me/onboarded", { method: "POST" })).resolves.toBeUndefined();
  });

  it("ошибка → ApiError со статусом и detail", async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse(401, { detail: "Invalid token" }));
    const err = await api("/me").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(401);
    expect((err as ApiError).message).toBe("Invalid token");
  });

  it("битое JSON-тело → statusText как сообщение", async () => {
    fetchMock.mockResolvedValueOnce({
      status: 502,
      statusText: "Bad Gateway",
      ok: false,
      json: () => Promise.reject(new Error("not json")),
    } as unknown as Response);
    const err = await api("/x").catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).message).toBe("Bad Gateway");
  });
});

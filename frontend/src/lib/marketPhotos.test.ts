/**
 * Тесты загрузки фото лота (lib/marketPhotos, 07-sell-chats-partner-
 * inspection.md §1): POST /api/market/photos — multipart FormData (поле
 * file), Authorization из localStorage, 401 → refresh-триггер + один
 * ретрай, ошибки бэка → detail в сообщении.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  api: vi.fn(), // api-клиент (триггер авто-refresh при 401)
}));

vi.mock("../api/client", async (importOriginal) => {
  // getAccessToken берём реальный — читает localStorage "autobot.access"
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, api: mocks.api };
});

import { isAcceptablePhoto, MAX_PHOTO_BYTES, uploadMarketPhoto } from "./marketPhotos";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.clearAllMocks();
  localStorage.setItem("autobot.access", "jwt-token-1");
  globalThis.fetch = fetchMock as unknown as typeof fetch;
});

afterEach(() => {
  localStorage.removeItem("autobot.access");
});

const okPhoto = () => new File(["x".repeat(10)], "car.jpg", { type: "image/jpeg" });
const resp = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("uploadMarketPhoto", () => {
  it("POST /api/market/photos с FormData (поле file) и Bearer-токеном → url", async () => {
    fetchMock.mockResolvedValueOnce(resp(200, { url: "http://localhost:9000/photos/1/uuid.jpg" }));
    const url = await uploadMarketPhoto(okPhoto());
    expect(url).toBe("http://localhost:9000/photos/1/uuid.jpg");

    const [calledUrl, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(calledUrl).toBe("/api/market/photos");
    expect(init.method).toBe("POST");
    // Content-Type НЕ задан руками — браузер сам ставит multipart boundary
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
    expect((init.headers as Record<string, string>).Authorization).toBe("Bearer jwt-token-1");
    // FormData с файлом в поле file (контракт §1)
    const fd = init.body as FormData;
    expect(fd).toBeInstanceOf(FormData);
    expect(fd.get("file")).toBeInstanceOf(File);
    expect((fd.get("file") as File).name).toBe("car.jpg");
  });

  it("401 → refresh-триггер (api /me) и один ретрай с новым токеном", async () => {
    fetchMock
      .mockResolvedValueOnce(resp(401, { detail: "Unauthorized" }))
      .mockResolvedValueOnce(resp(200, { url: "https://s3/ok.jpg" }));
    // «refresh» api-клиента обновил access-токен в хранилище
    mocks.api.mockImplementation(async () => {
      localStorage.setItem("autobot.access", "jwt-token-2");
      return undefined;
    });
    const url = await uploadMarketPhoto(okPhoto());
    expect(url).toBe("https://s3/ok.jpg");
    // api-клиент дёргался ровно один раз — как триггер авто-refresh
    expect(mocks.api).toHaveBeenCalledWith("/me");
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, retryInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect((retryInit.headers as Record<string, string>).Authorization).toBe("Bearer jwt-token-2");
  });

  it("ошибка бэка (413) — throw с detail", async () => {
    fetchMock.mockResolvedValueOnce(resp(413, { detail: "file too large" }));
    await expect(uploadMarketPhoto(okPhoto())).rejects.toThrow("file too large");
  });

  it("нет url в ответе — тоже ошибка", async () => {
    fetchMock.mockResolvedValueOnce(resp(200, { ok: true }));
    await expect(uploadMarketPhoto(okPhoto())).rejects.toThrow("Загрузка фото не удалась");
  });
});

describe("isAcceptablePhoto (клиентские проверки контракта)", () => {
  it("jpeg/png/webp до 8 МБ — ок; прочие типы и перегруз — нет", () => {
    const mk = (type: string, size = 1024) =>
      new File(["x"], "f", { type }) as unknown as File & { size: number };

    expect(isAcceptablePhoto(mk("image/jpeg"))).toBe(true);
    expect(isAcceptablePhoto(mk("image/png"))).toBe(true);
    expect(isAcceptablePhoto(mk("image/webp"))).toBe(true);
    expect(isAcceptablePhoto(mk("image/gif"))).toBe(false);
    expect(isAcceptablePhoto(mk("application/pdf"))).toBe(false);

    const big = mk("image/jpeg", MAX_PHOTO_BYTES + 1);
    Object.defineProperty(big, "size", { value: MAX_PHOTO_BYTES + 1 });
    expect(isAcceptablePhoto(big)).toBe(false);
  });
});

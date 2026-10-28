/** Тесты persistentState: localStorage-режим и fallback в память.
 *  Модуль пере-импортируется в каждом тесте (resolveStorage кешируется
 *  при загрузке модуля), поэтому без верхнего статического импорта. */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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

beforeEach(() => {
  vi.resetModules();
});
afterEach(() => {
  vi.unstubAllGlobals();
});

describe("persistentState", () => {
  it("get/set/remove — roundtrip через localStorage", async () => {
    vi.stubGlobal("localStorage", mockStorage());
    const { persistentGet, persistentSet, persistentRemove } =
      await import("./persistentState");
    expect(persistentGet("k")).toBeNull();
    persistentSet("k", "v");
    expect(persistentGet("k")).toBe("v");
    persistentRemove("k");
    expect(persistentGet("k")).toBeNull();
  });

  it("значения переживают пере-инициализацию модуля (аналог F5)", async () => {
    const ls = mockStorage();
    vi.stubGlobal("localStorage", ls);
    const first = await import("./persistentState");
    first.persistentSet("autobot.access", "jwt");
    // «перезагрузка»: модульная карта чистится, стор тот же
    vi.resetModules();
    const second = await import("./persistentState");
    expect(second.persistentGet("autobot.access")).toBe("jwt");
  });

  it("без localStorage — тихий fallback на память в рамках сессии", async () => {
    vi.stubGlobal("localStorage", undefined);
    const { persistentGet, persistentSet } = await import("./persistentState");
    expect(persistentGet("k")).toBeNull();
    persistentSet("k", "v");
    expect(persistentGet("k")).toBe("v");
  });

  it("localStorage, кидающий на запись — не ломает хранилище", async () => {
    const broken = mockStorage();
    broken.setItem = () => {
      throw new Error("quota");
    };
    vi.stubGlobal("localStorage", broken);
    const { persistentGet, persistentSet } = await import("./persistentState");
    persistentSet("k", "v");
    expect(persistentGet("k")).toBe("v"); // из зеркала в памяти
  });
});

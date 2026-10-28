/** Тесты истории просмотров (localStorage, дедуп, cap, ключи гостя). */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  addView,
  clearViews,
  getViews,
  subscribeViews,
  userKey,
} from "./viewHistory";

describe("viewHistory", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("гость без токена: ключ guest, записи читаются", () => {
    expect(userKey()).toBe("guest");
    addView({ listing_id: 1, make: "Kia", model: "K5" });
    const views = getViews();
    expect(views).toHaveLength(1);
    expect(views[0].listing_id).toBe(1);
    expect(views[0].viewed_at).toBeTruthy();
  });

  it("sub из JWT становится ключом истории", () => {
    const payload = btoa(JSON.stringify({ sub: 111111 }));
    localStorage.setItem("autobot.access", `header.${payload}.sig`);
    expect(userKey()).toBe("111111");
    addView({ listing_id: 2 });
    expect(getViews().map((v) => v.listing_id)).toEqual([2]);
  });

  it("дедуп: повторный просмотр поднимает наверх, не дублируя", () => {
    addView({ listing_id: 1 });
    addView({ listing_id: 2 });
    addView({ listing_id: 1 });
    const ids = getViews().map((v) => v.listing_id);
    expect(ids).toEqual([1, 2]);
  });

  it("cap 50: старые записи вытесняются", () => {
    for (let i = 1; i <= 55; i++) addView({ listing_id: i });
    const views = getViews();
    expect(views).toHaveLength(50);
    expect(views[0].listing_id).toBe(55);
    expect(views[49].listing_id).toBe(6);
  });

  it("clearViews чистит и нотифицирует подписку", () => {
    const cb = vi.fn();
    const stop = subscribeViews(cb);
    addView({ listing_id: 1 });
    expect(cb).toHaveBeenCalled();
    clearViews();
    expect(getViews()).toHaveLength(0);
    stop();
  });
});

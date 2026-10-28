/**
 * Тесты панели «Мои объявления» (NAV-V2: вынесена из Профиля в таб «Авто»).
 *
 * 06-market-user-listings.md §6 + 10-listing-lifecycle.md §5: статус-пилюли
 * жизненного цикла (Черновик/На проверке/Одобрено—оплата/Опубликовано/
 * Отклонено/Истёк/Снято), причина+violations, CTA Продолжить/Исправить/
 * Редактировать → /sell?edit, «Оплатить размещение» → extend, expired
 * «Восстановить» → POST /submit, pending_patch-баннер; live listing_status
 * (событие → тост + перечитанные my-lots).
 *
 * 12-ai-trust §2 (F12): «Качество: N/100» с пороговым цветом + первый совет.
 */

import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MyLotsPanel } from "./MyLotsPanel";
import { api } from "../api/client";
import { chatStore } from "../lib/chatStore";
import { openInvoice, type InvoiceStatus } from "../lib/invoice";
import { ToastProvider } from "../ui/Toast";
import type { MyLot } from "../api/types";

vi.mock("../api/client", () => ({ api: vi.fn() }));
vi.mock("../api/auth", () => ({
  haptic: vi.fn(),
  isGuest: vi.fn(() => false),
}));
vi.mock("../lib/invoice", () => ({ openInvoice: vi.fn() }));
// Транспорт личного канала — стаб: live-подписка панели стартует
// синхронизацию chatStore, реальные ws/поллинг в этих тестах не нужны.
vi.mock("../lib/chatSocket", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../lib/chatSocket")>();
  return {
    ...actual,
    createChatTransport: vi.fn(async () => ({
      kind: "polling" as const,
      connect: async () => undefined,
      subscribe: vi.fn(),
      unsubscribe: vi.fn(),
      disconnect: vi.fn(),
      onEvent: () => () => undefined,
      onThreads: () => () => undefined,
      setActiveThread: vi.fn(),
    })),
  };
});

const apiMock = vi.mocked(api);
const openInvoiceMock = vi.mocked(openInvoice);

/** Лот рынка с переопределениями (контракт MyLotOut бэка). */
const lot = (over: Partial<MyLot> & { listing_id: number }): MyLot => ({
  listing_id: 1,
  placement_status: "free",
  is_active: true,
  placement_free_until: null,
  placement_paid_until: null,
  make: "Kia",
  model: "K5",
  year: 2022,
  price: 20_000_000,
  currency: "KRW",
  mileage_km: 30_000,
  images: ["https://img/lot.jpg"],
  url: null,
  owner_description: null,
  ...over,
});

/** Панель с моими лотами. */
const renderWithLots = (myLots: MyLot[]) => {
  apiMock.mockImplementation(async (path: string, opts?: RequestInit) => {
    if (path === "/market/my-lots") return myLots;
    if (path === "/market/lots/2/extend" && opts?.method === "POST")
      return { payment_id: 9, invoice_url: "https://t.me/star", amount: 20, currency: "XTR" };
    if (path === "/market/lots/13/extend" && opts?.method === "POST")
      return { payment_id: 10, invoice_url: "https://t.me/star13", amount: 20, currency: "XTR" };
    // «Восстановить» истёкшего (10 §4): POST /submit — синхронный вердикт
    if (path === "/market/lots/16/submit" && opts?.method === "POST")
      return { status: "accepted" };
    return undefined;
  });
  return render(
    <ToastProvider>
      <MemoryRouter initialEntries={["/auto"]} initialIndex={0}>
        <MyLotsPanel />
      </MemoryRouter>
    </ToastProvider>,
  );
};

beforeEach(() => vi.clearAllMocks());
afterEach(() => cleanup());

describe("«Мои объявления» — статусы placement (06 §3)", () => {
  it("пилюли: Включён (Pro) / Бесплатно до / Оплачено до / Требует оплаты / Снято", async () => {
    renderWithLots([
      lot({ listing_id: 1, placement_status: "included" }),
      lot({ listing_id: 2, placement_status: "free", placement_free_until: "2026-11-02T12:00:00Z" }),
      lot({ listing_id: 3, placement_status: "paid", placement_paid_until: "2026-11-09T12:00:00Z" }),
      lot({ listing_id: 4, placement_status: "needs_payment" }),
      lot({ listing_id: 5, placement_status: "expired", is_active: false }),
    ]);
    const panel = (await screen.findByText("Мои объявления")).closest("section");
    expect(panel?.textContent).toContain("Включён (Pro)");
    expect(panel?.textContent).toContain("Бесплатно до 02.11.2026");
    expect(panel?.textContent).toContain("Оплачено до 09.11.2026");
    expect(panel?.textContent).toContain("Требует оплаты");
    expect(panel?.textContent).toContain("Снято");
    // карточка лота: превью/название/цена
    expect(panel?.textContent).toContain("Kia K5, 2022");
    expect(panel?.querySelector(".mylot__img")?.getAttribute("src")).toBe("https://img/lot.jpg");
    // вход в форму размещения
    expect(screen.getByText("＋ Продать авто").closest("a")?.getAttribute("href")).toBe("/sell");
  });

  it("пустой список — подсказка + кнопка продажи", async () => {
    renderWithLots([]);
    expect(await screen.findByText(/Объявлений пока нет/)).toBeTruthy();
    expect(screen.getByText("＋ Продать авто")).toBeTruthy();
  });
});

describe("«Мои объявления» — «Продлить» — extend + Stars-инвойс (06 §2)", () => {
  it("выбор 1/4 нед → POST extend {weeks} → openInvoice → paid обновляет список", async () => {
    const cbRef: { cb?: (s: InvoiceStatus) => void } = {};
    openInvoiceMock.mockImplementation(
      (_url: string, cb: (s: InvoiceStatus) => void) => {
        cbRef.cb = cb;
        return true;
      },
    );
    renderWithLots([lot({ listing_id: 2, placement_status: "free", placement_free_until: "2026-11-02T12:00:00Z" })]);
    fireEvent.click(await screen.findByRole("button", { name: "Продлить" }));
    // раскрылись оба срока: 1 нед = 20★, 4 нед = 60★
    fireEvent.click(screen.getByRole("button", { name: "1 неделя · 20★" }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([p, o]) => p === "/market/lots/2/extend" && o?.method === "POST")).toBe(true),
    );
    const extendCall = apiMock.mock.calls.find(([p]) => p === "/market/lots/2/extend");
    expect(JSON.parse(String(extendCall?.[1]?.body))).toEqual({ weeks: 1 });
    // инвойс открыт нативно
    expect(openInvoiceMock).toHaveBeenCalledWith("https://t.me/star", expect.any(Function));
    // оплата прошла: тост + перечитанные my-lots
    cbRef.cb?.("paid");
    expect(await screen.findByText("✅ Размещение продлено")).toBeTruthy();
    await waitFor(() => {
      const reloads = apiMock.mock.calls.filter(([p]) => p === "/market/my-lots").length;
      expect(reloads).toBeGreaterThanOrEqual(2);
    });
    openInvoiceMock.mockRestore();
  });

  it("4 нед — тоже доступно одним тапом из раскрытия", async () => {
    openInvoiceMock.mockReturnValue(true);
    renderWithLots([lot({ listing_id: 2, placement_status: "needs_payment" })]);
    fireEvent.click(await screen.findByRole("button", { name: "Продлить" }));
    fireEvent.click(screen.getByRole("button", { name: "4 недели · 60★" }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([p]) => p === "/market/lots/2/extend")).toBe(true),
    );
    const extendCall = apiMock.mock.calls.find(([p]) => p === "/market/lots/2/extend");
    expect(JSON.parse(String(extendCall?.[1]?.body))).toEqual({ weeks: 4 });
    openInvoiceMock.mockRestore();
  });
});

describe("«Мои объявления» — «Снять с продажи» — confirm → DELETE (06 §2)", () => {
  it("confirm=true → DELETE /market/lots/{id} + тост; отказ — запроса нет", async () => {
    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderWithLots([lot({ listing_id: 2 })]);
    fireEvent.click(await screen.findByRole("button", { name: "Снять" }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([p, o]) => p === "/market/lots/2" && o?.method === "DELETE")).toBe(true),
    );
    expect(await screen.findByText("Объявление снято с продажи")).toBeTruthy();
    // подтверждение отменено — второго DELETE нет
    confirmSpy.mockReturnValue(false);
    fireEvent.click(screen.getByRole("button", { name: "Снять" }));
    const deletes = apiMock.mock.calls.filter(
      ([p, o]) => p === "/market/lots/2" && o?.method === "DELETE",
    );
    expect(deletes).toHaveLength(1);
    confirmSpy.mockRestore();
  });
});

// ---------- Статусы жизненного цикла (10-listing-lifecycle.md §5) ----------

describe("«Мои объявления» — статус-пилюли lifecycle (10 §5)", () => {
  it("Черновик / На проверке / Одобрено—оплата / Опубликовано / Отклонено / Истёк / Снято", async () => {
    renderWithLots([
      lot({ listing_id: 11, status: "draft" }),
      lot({ listing_id: 12, status: "moderation" }),
      lot({ listing_id: 13, status: "accepted", placement_status: "needs_payment" }),
      lot({ listing_id: 14, status: "accepted", placement_status: "free", placement_free_until: "2026-11-02T12:00:00Z" }),
      lot({ listing_id: 15, status: "rejected" }),
      lot({ listing_id: 16, status: "expired" }),
      lot({ listing_id: 17, status: "archived" }),
    ]);
    const panel = (await screen.findByText("Мои объявления")).closest("section");
    const text = panel?.textContent ?? "";
    expect(text).toContain("Черновик");
    expect(text).toContain("На проверке ⏳");
    expect(text).toContain("Одобрено — оплата");
    expect(text).toContain("Опубликовано");
    expect(text).toContain("Отклонено");
    expect(text).toContain("Срок истёк");
    expect(text).toContain("Снято");
    // placement-подпись опубликованного — вторая строка под пилюлей
    expect(text).toContain("бесплатно до 02.11.2026");
    // «На проверке» — без действий; черновик/отклонено — свои CTA ниже
  });

  it("CTA «Продолжить»/«Исправить»/«Редактировать» ведут на /sell?edit={id}", async () => {
    renderWithLots([
      lot({ listing_id: 21, status: "draft" }),
      lot({ listing_id: 22, status: "rejected", rejected_reason: "нет фото" }),
      lot({ listing_id: 23, status: "accepted", placement_status: "free", placement_free_until: "2026-12-01T12:00:00Z" }),
    ]);
    expect((await screen.findByRole("link", { name: "Продолжить" })).getAttribute("href")).toBe("/sell?edit=21");
    expect(screen.getByRole("link", { name: "Исправить" }).getAttribute("href")).toBe("/sell?edit=22");
    expect(screen.getByRole("link", { name: "Редактировать" }).getAttribute("href")).toBe("/sell?edit=23");
  });

  it("Отклонено: причина + violations списком (10 §2)", async () => {
    renderWithLots([
      lot({
        listing_id: 24,
        status: "rejected",
        rejected_reason: "Нарушение правил размещения",
        violations: [
          { rule: "contacts_in_text", detail: "+82 10 1234 5678 в описании" },
          { rule: "forbidden_words", detail: "запрещённое слово" },
        ],
      }),
    ]);
    const panel = (await screen.findByText("Мои объявления")).closest("section");
    const text = panel?.textContent ?? "";
    expect(text).toContain("Нарушение правил размещения");
    expect(text).toContain("Контакты в описании: +82 10 1234 5678 в описании");
    expect(text).toContain("Запрещённые слова: запрещённое слово");
    expect(panel?.querySelector(".mylot__violations li")).toBeTruthy();
  });

  it("pending_patch: баннер старой версии; patch_reject_reason + «Исправить»", async () => {
    renderWithLots([
      lot({
        listing_id: 25,
        status: "accepted",
        placement_status: "free",
        placement_free_until: "2026-12-01T12:00:00Z",
        pending_patch: { patch_reject_reason: "телефон в описании" },
      }),
    ]);
    const panel = (await screen.findByText("Мои объявления")).closest("section");
    expect(panel?.textContent).toContain("Правки на проверке — в ленте продолжает показываться старая версия объявления");
    expect(panel?.textContent).toContain("Предыдущие правки отклонены: телефон в описании");
    expect(screen.getByRole("link", { name: "Исправить" }).getAttribute("href")).toBe("/sell?edit=25");
  });

  it("«Одобрено — оплата»: CTA «Оплатить размещение» → 1 нед → extend", async () => {
    openInvoiceMock.mockReturnValue(true);
    renderWithLots([
      lot({ listing_id: 13, status: "accepted", placement_status: "needs_payment" }),
    ]);
    fireEvent.click(await screen.findByRole("button", { name: "Оплатить размещение" }));
    // раскрылся выбор срока — тот же контур, что продление (06 §2)
    fireEvent.click(screen.getByRole("button", { name: "1 неделя · 20★" }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([p, o]) => p === "/market/lots/13/extend" && o?.method === "POST")).toBe(true),
    );
    const extendCall = apiMock.mock.calls.find(([p]) => p === "/market/lots/13/extend");
    expect(JSON.parse(String(extendCall?.[1]?.body))).toEqual({ weeks: 1 });
    openInvoiceMock.mockRestore();
  });

  it("expired «Восстановить» → POST /submit → тост + перечитанные my-lots", async () => {
    renderWithLots([lot({ listing_id: 16, status: "expired" })]);
    fireEvent.click(await screen.findByRole("button", { name: "Восстановить" }));
    await waitFor(() =>
      expect(apiMock.mock.calls.some(([p, o]) => p === "/market/lots/16/submit" && o?.method === "POST")).toBe(true),
    );
    expect(await screen.findByText("Объявление восстановлено")).toBeTruthy();
    await waitFor(() => {
      const reloads = apiMock.mock.calls.filter(([p]) => p === "/market/my-lots").length;
      expect(reloads).toBeGreaterThanOrEqual(2);
    });
  });
});

// ---------- Качество объявления в my-lots (12-ai-trust §2) ----------

describe("«Мои объявления» — качество объявления (12 §2)", () => {
  it("«Качество: N/100» с пороговым цветом (≥80 deal / ≥50 warn / ниже danger)", async () => {
    renderWithLots([
      lot({ listing_id: 41, status: "accepted", quality: { score: 80, tips: [] } }),
      lot({ listing_id: 42, status: "accepted", quality: { score: 55, tips: [] } }),
      lot({ listing_id: 43, status: "accepted", quality: { score: 20, tips: [] } }),
    ]);
    const q80 = await screen.findByText("Качество: 80/100");
    expect(q80.className).toContain("mylot__quality--deal");
    expect(screen.getByText("Качество: 55/100").className).toContain("mylot__quality--warn");
    expect(screen.getByText("Качество: 20/100").className).toContain("mylot__quality--danger");
  });

  it("первый tip — подсказкой под score-пилюлей; без quality строки нет", async () => {
    renderWithLots([
      lot({
        listing_id: 44,
        status: "accepted",
        quality: { score: 60, tips: ["Добавьте фото салона и кузова", "Опишите комплектацию"] },
      }),
      lot({ listing_id: 45, status: "accepted", quality: null }),
    ]);
    expect(await screen.findByText("Качество: 60/100")).toBeTruthy();
    // только первый совет, второго нет
    expect(screen.getByText("💡 Добавьте фото салона и кузова")).toBeTruthy();
    expect(screen.queryByText(/Опишите комплектацию/)).toBeNull();
    // quality=null (старый контракт) — строки качества нет
    expect(screen.queryByText("Качество: 45/100")).toBeNull();
    const cards = document.querySelectorAll(".mylot");
    expect(cards).toHaveLength(2);
    expect(cards[1].querySelector(".mylot__quality")).toBeNull();
  });
});

describe("«Мои объявления» — live listing_status (10 §3)", () => {
  it("событие личного канала → тост + перечитанные my-lots", async () => {
    renderWithLots([lot({ listing_id: 31, status: "moderation" })]);
    expect(await screen.findByText("На проверке ⏳")).toBeTruthy();
    const reloadsBefore = apiMock.mock.calls.filter(([p]) => p === "/market/my-lots").length;
    act(() => {
      chatStore.handleEvent({ type: "listing_status", listing_id: 31, status: "accepted" });
    });
    expect(await screen.findByText("Объявление одобрено")).toBeTruthy();
    await waitFor(() => {
      const reloads = apiMock.mock.calls.filter(([p]) => p === "/market/my-lots").length;
      expect(reloads).toBeGreaterThan(reloadsBefore);
    });
  });

  it("rejected с причиной — тост-ошибка; статус-пилюля обновляется после refresh", async () => {
    renderWithLots([lot({ listing_id: 32, status: "moderation" })]);
    await screen.findByText("На проверке ⏳");
    act(() => {
      chatStore.handleEvent({
        type: "listing_status",
        listing_id: 32,
        status: "rejected",
        reason: "контакты в описании",
      });
    });
    expect(await screen.findByText("Объявление отклонено: контакты в описании")).toBeTruthy();
  });
});

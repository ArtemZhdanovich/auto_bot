/**
 * Юнит-тесты партнёрского слоя: маппинг ошибок §7.9 (X2), хранение ключа
 * с «Запомнить» (X5), нормализаторы контрактов, форматтеры (P6/R4) и
 * компоненты StatusPill/QuotaMeter/Sheet (04 §6.2).
 */
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PartnerApiError,
  batchClaimCap,
  clearPartnerKey,
  getPartnerKey,
  isPartnerKeyRemembered,
  leadsAffordable,
  papi,
  partnerFail,
  setPartnerKey,
  toBillingOut,
  toMarketOut,
} from "./api";
import {
  QuotaMeter,
  Sheet,
  StatusPill,
  fmtMoneyCompact,
  fmtRelTime,
  fmtUsd,
  leadsPlural,
} from "./components/bits";

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  localStorage.clear();
  vi.unstubAllGlobals();
});

describe("partnerFail: HTTP-коды → русские строки §7.9 (X2)", () => {
  it("402 — про баланс, с видом действия «balance»", () => {
    const f = partnerFail(new PartnerApiError(402, "Insufficient balance: top up to claim leads"));
    expect(f.kind).toBe("balance");
    expect(f.text).toBe("Недостаточно средств на балансе. Пополните счёт у менеджера, чтобы забирать лиды.");
  });

  it("429 различает квоту и rate limit по detail", () => {
    expect(partnerFail(new PartnerApiError(429, "Daily quota exceeded")).kind).toBe("quota");
    expect(partnerFail(new PartnerApiError(429, "Rate limit exceeded")).kind).toBe("rate");
  });

  it("404/409 — лид занят другим; 5xx — сервис; TypeError — сеть", () => {
    expect(partnerFail(new PartnerApiError(404, "Lead not found")).kind).toBe("taken");
    expect(partnerFail(new PartnerApiError(409, "taken")).kind).toBe("taken");
    expect(partnerFail(new PartnerApiError(502, "boom")).kind).toBe("server");
    expect(partnerFail(new TypeError("fetch failed")).kind).toBe("network");
    expect(partnerFail(new Error("wat")).kind).toBe("other");
  });

  it("papi: сетевой сбой fetch → PartnerApiError(0) вместо TypeError", async () => {
    setPartnerKey("pi_x", false);
    vi.stubGlobal("fetch", vi.fn(async () => { throw new TypeError("fetch failed"); }));
    await expect(papi("/me")).rejects.toMatchObject({ status: 0 });
  });
});

describe("хранение ключа: «Запомнить на этом устройстве» (X5)", () => {
  it("remember=false → sessionStorage, ключ не переживает браузер", () => {
    setPartnerKey("pi_session", false);
    expect(getPartnerKey()).toBe("pi_session");
    expect(localStorage.getItem("autobot.partnerKey")).toBeNull();
    expect(isPartnerKeyRemembered()).toBe(false);
  });

  it("remember=true → localStorage; clear снимает оба хранилища", () => {
    setPartnerKey("pi_saved", true);
    expect(getPartnerKey()).toBe("pi_saved");
    expect(isPartnerKeyRemembered()).toBe(true);
    clearPartnerKey();
    expect(getPartnerKey()).toBeNull();
    expect(localStorage.getItem("autobot.partnerKey")).toBeNull();
    expect(sessionStorage.getItem("autobot.partnerKey")).toBeNull();
  });
});

describe("арифметика CPL (D1/P5)", () => {
  it("batchClaimCap = min(квота, баланс/CPL, 10)", () => {
    expect(batchClaimCap(8, 240, 12)).toBe(8); // режет квота
    expect(batchClaimCap(20, 96, 12)).toBe(8); // режет баланс (96/12)
    expect(batchClaimCap(20, 10_000, 12)).toBe(10); // потолок
    expect(batchClaimCap(0, 500, 12)).toBe(0); // квота исчерпана
    expect(batchClaimCap(5, 11, 12)).toBe(0); // денег нет даже на один
  });

  it("leadsAffordable и склонение лид/лида/лидов", () => {
    expect(leadsAffordable(240, 12)).toBe(20);
    expect(leadsAffordable(11, 12)).toBe(0);
    expect([leadsPlural(1), leadsPlural(2), leadsPlural(5), leadsPlural(20), leadsPlural(11)]).toEqual([
      "лид",
      "лида",
      "лидов",
      "лидов",
      "лидов",
    ]);
  });
});

describe("нормализаторы контрактов", () => {
  it("toBillingOut: balance/cpl_cost и каталог тарифов; без transactions — undefined", () => {
    const b = toBillingOut({
      plan: "pro",
      daily_quota: 20,
      claimed_today: 12,
      quota_remaining: 8,
      balance: "240.5",
      cpl_cost: "12",
      plans: [{ key: "pro", title: "Pro", daily_quota: 20, cpl_cost: "12", description: "d" }],
    });
    expect(b.balance).toBe(240.5);
    expect(b.cpl_cost).toBe(12);
    expect(b.plans[0].cpl_cost).toBe(12);
    expect(b.transactions).toBeUndefined();
  });

  it("toBillingOut: секция транзакций рисуется, когда API начнёт отдавать (§4.5.3)", () => {
    const b = toBillingOut({
      plan: "pro",
      daily_quota: 20,
      claimed_today: 0,
      quota_remaining: 20,
      balance: 100,
      cpl_cost: 10,
      plans: [],
      transactions: [
        { id: 1, kind: "topup", amount: 100, created_at: "2026-10-20T10:00:00Z", comment: "старт" },
        { id: 2, kind: "charge", amount: -10, created_at: "2026-10-21T10:00:00Z", comment: "лид #7" },
      ],
    });
    expect(b.transactions).toHaveLength(2);
    expect(b.transactions![0].amount).toBe(100);
  });

  it("toMarketOut: плоский и вложенный форматы сводки", () => {
    const flat = toMarketOut({ count: 10, min_price: 1, max_price: 9, median_price: 5, deals: [{ id: 1 }] });
    expect(flat).toMatchObject({ count: 10, median: 5 });
    expect(flat.items).toHaveLength(1);
    const nested = toMarketOut({ summary: { count: 3, median_price: 7 }, items: [] });
    expect(nested.count).toBe(3);
    expect(nested.median).toBe(7);
  });
});

describe("форматтеры (P6/R4)", () => {
  const NOW = Date.parse("2026-10-24T12:00:00Z");

  it("fmtRelTime: минуты, «сегодня ЧЧ:ММ», «вчера»", () => {
    const at = (min: number) => new Date(NOW - min * 60_000).toISOString();
    expect(fmtRelTime(at(5), NOW)).toBe("5 мин назад");
    expect(fmtRelTime(at(90), NOW)).toMatch(/^сегодня \d{2}:\d{2}$/);
    expect(fmtRelTime(new Date(NOW - 26 * 3_600_000).toISOString(), NOW)).toMatch(/^вчера \d{2}:\d{2}$/);
    expect(fmtRelTime(null, NOW)).toBe("—");
  });

  it("fmtMoneyCompact: миллионы и тысячи компактно, null → «—»", () => {
    expect(fmtMoneyCompact(24_300_000, "KRW")).toBe("24,3 млн KRW");
    expect(fmtMoneyCompact(180_000_000, "KRW")).toBe("180 млн KRW");
    expect(fmtMoneyCompact(920_000, "KRW")).toBe("920 тыс. KRW");
    expect(fmtMoneyCompact(21_400)).toBe("21 400");
    expect(fmtMoneyCompact(null)).toBe("—");
  });

  it("fmtUsd: целые без копеек, дробные с ними", () => {
    expect(fmtUsd(240)).toBe("$240");
    expect(fmtUsd(12.5)).toBe("$12.5");
    expect(fmtUsd(null)).toBe("—");
  });
});

describe("StatusPill: цвет + слово, raw-фолбэк (M3)", () => {
  it("жизненный цикл лида читается словами", () => {
    const { container } = render(
      <>
        <StatusPill status="claimed" />
        <StatusPill status="accepted" />
        <StatusPill status="deal" />
        <StatusPill status="returned" />
        <StatusPill status="junk" />
        <StatusPill status="weird_future_status" />
      </>,
    );
    const texts = Array.from(container.querySelectorAll(".pa-pill")).map((el) => el.textContent);
    expect(texts).toEqual(["Новый", "Принят", "Сделка", "Возврат", "Брак", "weird_future_status"]);
  });
});

describe("QuotaMeter: предупреждение заранее, не по факту 429 (B3)", () => {
  it("≥80% — warn с подписью, 100% — «обновится завтра»", () => {
    const { container, rerender } = render(
      <QuotaMeter claimed={12} quota={20} remaining={8} />,
    );
    expect(container.querySelector(".pa-quota__fill--warn")).toBeNull(); // 60% — норма
    expect(container.querySelector('[role="progressbar"]')?.getAttribute("aria-valuenow")).toBe("12");

    rerender(<QuotaMeter claimed={18} quota={20} remaining={2} />);
    expect(container.querySelector(".pa-quota__fill--warn")).toBeTruthy();
    expect(screen.getByText("Квота почти исчерпана")).toBeTruthy();

    rerender(<QuotaMeter claimed={20} quota={20} remaining={0} />);
    expect(container.querySelector(".pa-quota__fill--done")).toBeTruthy();
    expect(screen.getByText("Квота исчерпана — обновится завтра в 00:00")).toBeTruthy();
  });
});

describe("Sheet: подтверждения закрываются по Esc, aria-modal (02 §7.10)", () => {
  it("открыт → dialog с aria-modal; Esc закрывает", () => {
    const onClose = vi.fn();
    const { rerender } = render(
      <Sheet open onClose={onClose} title="Отметить «Брак»?">
        текст
      </Sheet>,
    );
    expect(screen.getByRole("dialog", { name: 'Отметить «Брак»?' }).getAttribute("aria-modal")).toBe("true");

    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);

    rerender(
      <Sheet open={false} onClose={onClose} title="t">
        текст
      </Sheet>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

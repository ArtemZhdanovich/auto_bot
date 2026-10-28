/** Юнит-тесты маппинга API-ошибок (03-content-a11y.md §3.2). */

import { describe, expect, it } from "vitest";

import { apiErrorText, apiErrorView } from "./apiErrorText";

/** Локальный ApiError-двойник (импортить реальный не нужно — хелпер
 *  смотрит только на поля status/message). */
const err = (status: number | undefined, message: string) =>
  Object.assign(new Error(message), { status });

describe("apiErrorView: маппинг по префиксу detail и статусу", () => {
  it("403 quota_exceeded → апселл «Безлимит в Pro» → /profile", () => {
    const v = apiErrorView(err(403, "quota_exceeded: Free 1 в неделю"));
    expect(v.text).toContain("Бесплатный разбор этой недели уже использован");
    expect(v.action?.label).toBe("⚡ Безлимит в Pro");
    expect(v.action?.to).toBe("/profile");
  });

  it("403 Filter limit reached → лимит Free-3 + «Смотреть тарифы»", () => {
    const v = apiErrorView(err(403, "Filter limit reached (3 on free tier)"));
    expect(v.text).toContain("Лимит уведомлений Free — 3");
    expect(v.action?.label).toBe("Смотреть тарифы");
  });

  it("422 price_min → человеческий текст про границы цены", () => {
    expect(apiErrorText(err(422, "price_min > price_max"))).toBe(
      "«Цена от» больше «цены до». Поменяйте границы местами или очистите одно из полей.",
    );
  });

  it("503 AI → «AI-агент отдыхает» + фолбэк к сигналам", () => {
    const v = apiErrorView(err(503, "AI temporarily unavailable"));
    expect(v.text).toContain("AI-агент отдыхает");
    expect(v.action?.to).toBe("/filters");
  });

  it("503 оплата → «Платежи временно недоступны»", () => {
    expect(apiErrorText(err(503, "Telegram invoice creation failed"))).toContain(
      "Платежи временно недоступны",
    );
  });

  it("429 → подождать минуту", () => {
    expect(apiErrorText(err(429, "rate limit"))).toContain("Слишком много вопросов подряд");
  });

  it("сеть (TypeError без статуса) → «Нет соединения»", () => {
    expect(apiErrorText(new TypeError("Failed to fetch"))).toContain("Нет соединения");
  });

  it("неизвестный detail → «Что-то пошло не так»", () => {
    expect(apiErrorText(err(500, "weird internal stuff"))).toContain("Что-то пошло не так");
    // сырой detail наружу не отдаём
    expect(apiErrorText(err(500, "weird internal stuff"))).not.toContain("weird");
  });
});

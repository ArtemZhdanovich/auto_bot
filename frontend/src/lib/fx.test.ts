/** Юнит-тесты чистых хелперов миниаппа (vitest, node — без React). */
import { describe, expect, it } from "vitest";

import { EUR_PER_USD, KRW_PER_USD, digits, parseQueryVariants, toUsd } from "./fx";

describe("toUsd", () => {
  it("KRW делит на 1350 и округляет", () => {
    expect(toUsd(18_400_000, "KRW")).toBe(Math.round(18_400_000 / KRW_PER_USD));
    expect(toUsd(1_000_000, "krw")).toBe(741); // регистр не важен
  });

  it("EUR умножает на 1.08", () => {
    expect(toUsd(10_000, "EUR")).toBe(Math.round(10_000 * EUR_PER_USD));
    expect(toUsd(10_000, "eur")).toBe(10_800);
  });

  it("USD проходит как есть (округление)", () => {
    expect(toUsd(18_000, "USD")).toBe(18_000);
    expect(toUsd(18000.4)).toBe(18_000); // валюта не указана → USD
  });

  it("мусор → null", () => {
    expect(toUsd(null)).toBeNull();
    expect(toUsd(0)).toBeNull();
    expect(toUsd(-5)).toBeNull();
    expect(toUsd(undefined, "KRW")).toBeNull();
  });
});

describe("digits", () => {
  it("оставляет только цифры", () => {
    expect(digits("18 000")).toBe("18000");
    expect(digits("abc12x34")).toBe("1234");
    expect(digits("")).toBe("");
  });
});

describe("parseQueryVariants", () => {
  it("парсит deep-link ?v=...", () => {
    expect(parseQueryVariants("18000x2000x2022,15000x1600x2021")).toEqual([
      { price: "18000", cc: "2000", year: "2022" },
      { price: "15000", cc: "1600", year: "2021" },
    ]);
  });

  it("подставляет cc=2000 по умолчанию", () => {
    expect(parseQueryVariants("18000x2022")).toEqual([
      { price: "18000", cc: "2000", year: "2022" },
    ]);
  });

  it("null/мусор → пустой список", () => {
    expect(parseQueryVariants(null)).toEqual([]);
    expect(parseQueryVariants("abc,def")).toEqual([]);
  });

  it("пропускает только битые куски, годные оставляет", () => {
    expect(parseQueryVariants("xx,18000x2000x2022")).toEqual([
      { price: "18000", cc: "2000", year: "2022" },
    ]);
  });
});

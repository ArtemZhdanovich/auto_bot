/**
 * Сравнение вариантов «под ключ» (MVP): 2–4 варианта, бары, дельта к лучшему.
 * Сид вариантами: location.state.seed {price, cc, year} из калькулятора
 * или query ?v=18000x2000x2022,15000x1600x2021 (deep-link из бота).
 */

import { Button } from "@telegram-apps/telegram-ui";
import { useCallback, useEffect, useState } from "react";
import { useLocation, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import { digits, parseQueryVariants, type Variant } from "../lib/fx";

interface CompareItem {
  title: string;
  price_usd: number;
  total_usd: number;
  total_rub: number;
  age_group: string;
}

const fmt = (n: number) => Math.round(n).toLocaleString("ru-RU");

export function CompareScreen() {
  const location = useLocation();
  const [params] = useSearchParams();
  const seed = (location.state as { seed?: Variant } | null)?.seed;
  const fromQuery = parseQueryVariants(params.get("v"));

  const [variants, setVariants] = useState<Variant[]>(
    fromQuery.length
      ? fromQuery
      : seed
        ? [seed, { price: "", cc: "2000", year: "" }]
        : [
            { price: "", cc: "2000", year: "" },
            { price: "", cc: "2000", year: "" },
          ],
  );
  const [items, setItems] = useState<CompareItem[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const update = (i: number, field: keyof Variant, value: string) => {
    setVariants((prev) =>
      prev.map((v, idx) => (idx === i ? { ...v, [field]: digits(value) } : v)),
    );
  };

  const compare = useCallback(async (vs: Variant[]) => {
    const payload = vs
      .filter((v) => digits(v.price) && digits(v.year))
      .map((v, i) => ({
        price_usd: Number(digits(v.price)),
        engine_cc: Number(digits(v.cc)) || 2000,
        year: Number(digits(v.year)),
        title: `Вариант ${i + 1}`,
      }));
    if (payload.length < 2) {
      setError("Заполните минимум два варианта (цена и год)");
      return;
    }
    setLoading(true);
    setError(null);
    haptic("light");
    try {
      const out = await api<{ items: CompareItem[] }>("/calc/compare", {
        method: "POST",
        body: JSON.stringify({ items: payload }),
      });
      setItems(out.items);
      haptic("medium");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Автосравнение при входе с сид-данными (из калькулятора или deep-link)
  useEffect(() => {
    const ready = (fromQuery.length ? fromQuery : seed ? [seed] : []).filter(
      (v) => digits(v.price) && digits(v.year),
    );
    if (ready.length) void compare(ready);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const maxTotal = items ? Math.max(...items.map((i) => i.total_usd)) : 1;

  return (
    <div className="calc">
      <div className="feed-header">
        <div className="feed-header__title">⚖️ Сравнение «под ключ»</div>
        <div className="feed-header__counter">
          Пошлина зависит от возраста и объёма — итог может удивить
        </div>
      </div>

      <div className="calc-form">
        {variants.map((v, i) => (
          <div key={i} className="compare-variant">
            <div className="compare-variant__label">Вариант {i + 1}</div>
            <div className="compare-variant__fields">
              <label className="calc-field">
                <span>Цена, $</span>
                <input
                  inputMode="numeric"
                  value={v.price ? Number(v.price).toLocaleString("ru-RU") : ""}
                  onChange={(e) => update(i, "price", e.target.value)}
                  placeholder="18 000"
                />
              </label>
              <label className="calc-field">
                <span>Объём, см³</span>
                <input
                  inputMode="numeric"
                  value={v.cc ? Number(v.cc).toLocaleString("ru-RU") : ""}
                  onChange={(e) => update(i, "cc", e.target.value)}
                  placeholder="2 000"
                />
              </label>
              <label className="calc-field">
                <span>Год</span>
                <input
                  inputMode="numeric"
                  value={v.year}
                  onChange={(e) => update(i, "year", e.target.value)}
                  placeholder="2022"
                />
              </label>
            </div>
            {variants.length > 2 && (
              <button
                type="button"
                className="compare-remove"
                onClick={() => {
                  haptic("light");
                  setVariants((prev) => prev.filter((_, idx) => idx !== i));
                }}
              >
                ✕ убрать
              </button>
            )}
          </div>
        ))}

        <div className="chip-row">
          {variants.length < 4 && (
            <button
              type="button"
              className="chip"
              onClick={() => {
                haptic("light");
                setVariants((prev) => [...prev, { price: "", cc: "2000", year: "" }]);
              }}
            >
              + вариант
            </button>
          )}
        </div>

        <Button size="l" stretched loading={loading} onClick={() => void compare(variants)}>
          Сравнить
        </Button>
      </div>

      {error && (
        <div className="state">
          <div className="state__title">{error}</div>
        </div>
      )}

      {items && items.length >= 2 && (
        <div className="calc-result">
          {items.map((it, i) => (
            <div
              key={it.title}
              className={i === 0 ? "compare-item compare-item--best" : "compare-item"}
            >
              <div className="compare-item__head">
                <span>
                  {i === 0 ? "🥇 " : `${i + 1}. `}
                  {it.title}
                  {i === 0 && <span className="compare-badge">лучший итог</span>}
                </span>
                <b>{fmt(it.total_usd)} $</b>
              </div>
              <div className="compare-bar">
                <div
                  className="compare-bar__fill"
                  style={{ width: `${(it.total_usd / maxTotal) * 100}%` }}
                />
              </div>
              <div className="compare-item__sub">
                авто {fmt(it.price_usd)} $ · {it.age_group} · <b>{fmt(it.total_rub)} ₽</b>
                {i > 0 && (
                  <span className="compare-delta">
                    {" "}(+{fmt(it.total_usd - items[0].total_usd)} $ к лучшему)
                  </span>
                )}
              </div>
            </div>
          ))}
          <div className="calc-note">
            🚢 Море Бусан → Владивосток + брокер + СБКТС/ЭПТС + пошлина + утильсбор уже
            включены в итог.
          </div>
        </div>
      )}
    </div>
  );
}

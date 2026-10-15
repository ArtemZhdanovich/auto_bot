/**
 * Калькулятор доставки Корея → РФ («под ключ»): авто + море + брокер +
 * СБКТС/ЭПТС + пошлина + сбор + утильсбор. Префилл из query (?price=&cc=&year=).
 */

import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Button } from "@telegram-apps/telegram-ui";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import { SkeletonCard } from "../ui/Skeleton";

interface CalcComponent {
  label: string;
  usd: number;
  rub: number;
}

interface CalcOut {
  price_usd: number;
  engine_cc: number;
  year: number;
  age_years: number;
  age_group: string;
  components: CalcComponent[];
  total_rub: number;
  total_usd: number;
}

export function CalcScreen() {
  const [params] = useSearchParams();
  const [price, setPrice] = useState(params.get("price") ?? "");
  const [cc, setCc] = useState(params.get("cc") ?? "2000");
  const [year, setYear] = useState(params.get("year") ?? String(new Date().getFullYear() - 3));
  const [result, setResult] = useState<CalcOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const calc = useCallback(async (p: string, c: string, y: string) => {
    const priceNum = parseFloat(p.replace(/\s/g, ""));
    const ccNum = parseInt(c, 10);
    const yearNum = parseInt(y, 10);
    if (!priceNum || priceNum <= 0 || !yearNum || yearNum < 1980 || yearNum > new Date().getFullYear() + 1) {
      setError("Проверьте цену и год (1980 – след. год)");
      return;
    }
    setLoading(true);
    setError(null);
    haptic("light");
    try {
      const out = await api<CalcOut>("/calc/delivery", {
        method: "POST",
        body: JSON.stringify({
          price_usd: priceNum,
          engine_cc: ccNum || 2000,
          year: yearNum,
        }),
      });
      setResult(out);
      haptic("medium");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  // Префилл из deep-link (кнопка «Рассчитать доставку» в карточке)
  useEffect(() => {
    if (params.get("price")) void calc(params.get("price") ?? "", params.get("cc") ?? "2000", params.get("year") ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fmt = (n: number) => n.toLocaleString("ru-RU");
  /** Группировка тысяч при вводе: 18400 → 18 400 */
  const grouped = (raw: string) => {
    const digits = raw.replace(/\D/g, "");
    return digits ? Number(digits).toLocaleString("ru-RU") : "";
  };
  const CC_CHIPS = [1600, 2000, 2500, 3000];

  return (
    <div className="calc">
      <div className="feed-header">
        <div className="feed-header__title">🚚 Доставка «под ключ»</div>
        <div className="feed-header__counter">Корея → Владивосток → вам</div>
      </div>

      <div className="calc-form">
        <label className="calc-field">
          <span>Цена авто, $</span>
          <input
            inputMode="numeric"
            value={grouped(price)}
            onChange={(e) => setPrice(e.target.value.replace(/\D/g, ""))}
            placeholder="18 000"
          />
        </label>
        <label className="calc-field">
          <span>Объём двигателя, см³</span>
          <input
            inputMode="numeric"
            value={grouped(cc)}
            onChange={(e) => setCc(e.target.value.replace(/\D/g, ""))}
            placeholder="2 000"
          />
        </label>
        <div className="chip-row">
          {CC_CHIPS.map((v) => (
            <button
              key={v}
              type="button"
              className={cc === String(v) ? "chip chip--active" : "chip"}
              onClick={() => {
                haptic("light");
                setCc(String(v));
              }}
            >
              {v} см³
            </button>
          ))}
        </div>
        <label className="calc-field">
          <span>Год выпуска</span>
          <input
            inputMode="numeric"
            value={year}
            onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))}
            placeholder="2022"
          />
        </label>
        <Button size="l" stretched loading={loading} onClick={() => void calc(price, cc, year)}>
          Рассчитать
        </Button>
      </div>

      {error && (
        <div className="state">
          <div className="state__title">{error}</div>
        </div>
      )}

      {loading && !result && (
        <div style={{ padding: 12 }}>
          <SkeletonCard />
        </div>
      )}

      {result && (
        <div className="calc-result">
          <div className="calc-total">
            <div className="calc-total__label">Итого «под ключ»</div>
            <div className="calc-total__value">{fmt(Math.round(result.total_rub))} ₽</div>
            <div className="calc-total__sub">≈ {fmt(Math.round(result.total_usd))} $</div>
            <div className="calc-total__badge">
              {result.year} г. · {result.engine_cc} см³ · {result.age_group}
            </div>
          </div>

          <div className="calc-rows">
            {result.components.map((c) => (
              <div key={c.label} className="calc-row">
                <span>{c.label}</span>
                <b>
                  {fmt(c.rub)} ₽
                  <small>≈ {fmt(Math.round(c.usd))} $</small>
                </b>
              </div>
            ))}
          </div>

          <div className="calc-note">
            Оценка для физлица (личное пользование), курс фиксированный.
            Логистика, брокер и СБКТС — средние по рынку. Точную смету даст менеджер.
          </div>

          <div className="calc-note">
            🚢 Море Бусан → Владивосток, далее по РФ — менеджер подберёт
            маршрут и точные сроки.
          </div>
        </div>
      )}
    </div>
  );
}

/**
 * Калькулятор доставки Корея → РФ («под ключ»): авто + море + брокер +
 * СБКТС/ЭПТС + пошлина + сбор + утильсбор. Префилл из query (?price=&cc=&year=).
 */

import { useCallback, useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import { getRegion } from "../lib/fx";
import { SkeletonCard } from "../ui/Skeleton";

interface CalcComponent {
  label: string;
  usd: number;
  rub: number;
}

/** Кнопка перехода к сравнению (bot → webapp идея: продолжай в удобном UI). */
function CompareBtn({ price, cc, hp, year }: { price: string; cc: string; hp: string; year: string }) {
  const navigate = useNavigate();
  const ready = Boolean(price && year);
  return (
    <button
      type="button"
      className="chip chip--ghost calc-compare-btn"
      disabled={!ready}
      onClick={() => {
        haptic("light");
        navigate("/compare", { state: { seed: { price, cc, hp, year } } });
      }}
    >
      ⚖️ Сравнить с другим вариантом
    </button>
  );
}

interface LegalRef {
  label: string;
  ref: string;
}

interface CalcOut {
  price_usd: number;
  engine_cc: number;
  year: number;
  age_years: number;
  age_group: string;
  power_hp?: number | null;
  util_category?: string;
  util_note?: string;
  components: CalcComponent[];
  legal_refs?: LegalRef[];
  total_rub: number;
  total_usd: number;
}

export function CalcScreen() {
  const [params] = useSearchParams();
  const [price, setPrice] = useState(params.get("price") ?? "");
  const [cc, setCc] = useState(params.get("cc") ?? "2000");
  const [hp, setHp] = useState(params.get("hp") ?? "");
  const [year, setYear] = useState(params.get("year") ?? String(new Date().getFullYear() - 3));
  const [result, setResult] = useState<CalcOut | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const calc = useCallback(async (p: string, c: string, h: string, y: string) => {
    const priceNum = parseFloat(p.replace(/\s/g, ""));
    const ccNum = parseInt(c, 10);
    const hpNum = parseInt(h, 10);
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
          power_hp: hpNum || undefined,
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
    if (params.get("price")) {
      void calc(
        params.get("price") ?? "",
        params.get("cc") ?? "2000",
        params.get("hp") ?? "",
        params.get("year") ?? "",
      );
    }
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
          <span>Мощность, л.с. (опционально)</span>
          <input
            inputMode="numeric"
            value={hp}
            onChange={(e) => setHp(e.target.value.replace(/\D/g, "").slice(0, 4))}
            placeholder="150"
          />
        </label>
        <label className="calc-field">
          <span>Год выпуска</span>
          <input
            inputMode="numeric"
            value={year}
            onChange={(e) => setYear(e.target.value.replace(/\D/g, "").slice(0, 4))}
            placeholder="2022"
          />
        </label>
        <button
          className="btn btn--primary btn--block"
          disabled={loading}
          onClick={() => void calc(price, cc, hp, year)}
        >
          {loading ? "Считаю…" : "Рассчитать"}
        </button>
        <CompareBtn price={price} cc={cc} hp={hp} year={year} />
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
              {result.power_hp ? `${result.power_hp} л.с. · ` : ""}
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

          {result.util_note && <div className="calc-warn">⚠️ {result.util_note}</div>}

          <div className="calc-note">
            Оценка для физлица (личное пользование, не более 1 авто в год), курс
            фиксированный. Льготный утильсбор действует при мощности до 160 л.с.
            и объёме до 3000 см³ (ставки сверены 16.10.2026). Точную смету даст
            менеджер.
          </div>

          <RegionNote />

          {result.legal_refs && result.legal_refs.length > 0 && (
            <div className="calc-legal">
              <div className="calc-legal__title">Основание расчёта</div>
              {result.legal_refs.map((r) => (
                <div key={r.label} className="calc-legal__row">
                  <span className="calc-legal__label">{r.label}</span>
                  <span className="calc-legal__ref">{r.ref}</span>
                </div>
              ))}
            </div>
          )}

          <div className="calc-note">
            🚢 Море Бусан → Владивосток, далее по РФ — менеджер подберёт
            маршрут и точные сроки.
          </div>
        </div>
      )}
    </div>
  );
}

/** Заметка по региону: платежи ЕАЭС одинаковы, регистрация — разная. */
function RegionNote() {
  const region = getRegion();
  if (region !== "by") return null;
  return (
    <div className="calc-note">
      🇧🇾 Беларусь: таможенные платежи ЕАЭС одинаковы с РФ, но при постановке на
      учёт в ГАИ действуют свои правила и утильсбор РБ — менеджер подскажет
      полный маршрут: порт Владивосток → Минск.
    </div>
  );
}

/** Уведомления (ТЗ п. 9.2.3): сохранённые критерии + сообщения о новых лотах. */

import { useCallback, useEffect, useState } from "react";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import type { Criteria, UserFilter } from "../api/types";
import {
  BODIES,
  DRIVES,
  FUELS,
  TRANSMISSIONS,
} from "../lib/specs";

interface FormState {
  name: string;
  make: string;
  model: string;
  yearFrom: string;
  yearTo: string;
  priceMin: string;
  priceMax: string;
  mileageMax: string;
  fuel: string;
  transmission: string;
  bodyType: string;
  drive: string;
}

const EMPTY: FormState = {
  name: "",
  make: "",
  model: "",
  yearFrom: "",
  yearTo: "",
  priceMin: "",
  priceMax: "",
  mileageMax: "",
  fuel: "",
  transmission: "",
  bodyType: "",
  drive: "",
};

const onlyDigits = (s: string) => s.replace(/\D/g, "").slice(0, 7);

function criteriaOf(f: FormState) {
  const num = (s: string) => (s ? Number(s) : undefined);
  return {
    ...(f.make ? { make: f.make } : {}),
    ...(f.model ? { model: f.model } : {}),
    ...(f.yearFrom ? { year_from: num(f.yearFrom) } : {}),
    ...(f.yearTo ? { year_to: num(f.yearTo) } : {}),
    ...(f.priceMin ? { price_min: num(f.priceMin) } : {}),
    ...(f.priceMax ? { price_max: num(f.priceMax) } : {}),
    ...(f.mileageMax ? { mileage_max: num(f.mileageMax) } : {}),
    ...(f.fuel ? { fuel: f.fuel } : {}),
    ...(f.transmission ? { transmission: f.transmission } : {}),
    ...(f.bodyType ? { body_type: f.bodyType } : {}),
    ...(f.drive ? { drive: f.drive } : {}),
  };
}

const CRITERIA_LABELS: Record<string, string> = {
  year_from: "от",
  year_to: "до",
  price_min: "от $",
  price_max: "до $",
  mileage_max: "до км",
  fuel: "топливо",
  transmission: "КПП",
  body_type: "кузов",
  drive: "привод",
};

function describe(criteria: Criteria): string {
  const parts: string[] = [];
  if (criteria.make) parts.push(criteria.make);
  if (criteria.model) parts.push(criteria.model);
  for (const [key, label] of Object.entries(CRITERIA_LABELS)) {
    const v = (criteria as Record<string, unknown>)[key];
    if (v != null) parts.push(`${label} ${v}`);
  }
  return parts.join(", ") || "все объявления";
}

export function FiltersScreen() {
  const [filters, setFilters] = useState<UserFilter[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState<FormState>(EMPTY);
  const [instant, setInstant] = useState(false);

  const set = (k: keyof FormState) => (v: string) =>
    setForm((p) => ({ ...p, [k]: v }));

  const reload = useCallback(async () => {
    setLoading(true);
    setFilters(await api<UserFilter[]>("/filters"));
    setLoading(false);
  }, []);

  useEffect(() => {
    void reload().catch(() => setLoading(false));
  }, [reload]);

  const create = async () => {
    const criteria = criteriaOf(form);
    if (Object.keys(criteria).length === 0) {
      setError("Заполните хотя бы один параметр поиска");
      return;
    }
    setCreating(true);
    setError(null);
    try {
      await api<UserFilter>("/filters", {
        method: "POST",
        body: JSON.stringify({
          name: form.name || null,
          criteria,
          notify_mode: instant ? "instant" : "digest",
        }),
      });
      haptic();
      setForm(EMPTY);
      setInstant(false);
      await reload();
    } catch (e) {
      const msg = (e as Error).message || "";
      setError(
        msg.includes("403") || msg.toLowerCase().includes("limit")
          ? "Достигнут лимит уведомлений тарифа Free — удалите ненужное или оформите Pro"
          : "Не удалось создать уведомление, попробуйте позже",
      );
    } finally {
      setCreating(false);
    }
  };

  const remove = async (id: number) => {
    await api<void>(`/filters/${id}`, { method: "DELETE" });
    haptic();
    await reload();
  };

  return (
    <div className="screen">
      <section className="panel">
        <h3>Новое уведомление</h3>
        <p className="filters-form__intro">
          Заполните, какой авто ищете, — и мы напишем вам в Telegram, когда
          такой появится в продаже.
        </p>
        <div className="filters-form">
          <label className="calc-field">
            <span>Название (необязательно)</span>
            <input value={form.name} onChange={(e) => set("name")(e.target.value)} placeholder="Семейный кроссовер" />
          </label>
          <div className="filter-pair">
            <label className="calc-field">
              <span>Марка</span>
              <input value={form.make} onChange={(e) => set("make")(e.target.value)} placeholder="BMW" />
            </label>
            <label className="calc-field">
              <span>Модель</span>
              <input value={form.model} onChange={(e) => set("model")(e.target.value)} placeholder="X5" />
            </label>
          </div>
          <div className="filter-pair">
            <label className="calc-field">
              <span>Год от</span>
              <input inputMode="numeric" value={form.yearFrom} onChange={(e) => set("yearFrom")(onlyDigits(e.target.value))} placeholder="2019" />
            </label>
            <label className="calc-field">
              <span>Год до</span>
              <input inputMode="numeric" value={form.yearTo} onChange={(e) => set("yearTo")(onlyDigits(e.target.value))} placeholder="2024" />
            </label>
          </div>
          <div className="filter-pair">
            <label className="calc-field">
              <span>Цена $ от</span>
              <input inputMode="numeric" value={form.priceMin} onChange={(e) => set("priceMin")(onlyDigits(e.target.value))} placeholder="15000" />
            </label>
            <label className="calc-field">
              <span>Цена $ до</span>
              <input inputMode="numeric" value={form.priceMax} onChange={(e) => set("priceMax")(onlyDigits(e.target.value))} placeholder="40000" />
            </label>
          </div>
          <label className="calc-field">
            <span>Пробег, км — не более</span>
            <input inputMode="numeric" value={form.mileageMax} onChange={(e) => set("mileageMax")(onlyDigits(e.target.value))} placeholder="100000" />
          </label>
          <div className="filter-pair">
            <label className="filter-select">
              <span>Топливо</span>
              <select value={form.fuel} onChange={(e) => set("fuel")(e.target.value)}>
                {FUELS.map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </label>
            <label className="filter-select">
              <span>КПП</span>
              <select value={form.transmission} onChange={(e) => set("transmission")(e.target.value)}>
                {TRANSMISSIONS.map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </label>
          </div>
          <div className="filter-pair">
            <label className="filter-select">
              <span>Кузов</span>
              <select value={form.bodyType} onChange={(e) => set("bodyType")(e.target.value)}>
                {BODIES.map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </label>
            <label className="filter-select">
              <span>Привод</span>
              <select value={form.drive} onChange={(e) => set("drive")(e.target.value)}>
                {DRIVES.map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </label>
          </div>
          <button
            className={instant ? "feed-toggle feed-toggle--on" : "feed-toggle"}
            aria-pressed={instant}
            onClick={() => setInstant((v) => !v)}
          >
            ⚡ Прислать сразу, как найдётся
          </button>
          <div className="filters-form__hint">
            {instant
              ? "Сообщим сразу, как только авто появится"
              : "Иначе пришлём подборкой раз в день"}
          </div>
          {error && <div className="form-error">{error}</div>}
          <button className="btn btn--primary btn--block" disabled={creating} onClick={() => void create()}>
            {creating ? "Создаём…" : "Создать уведомление"}
          </button>
        </div>
      </section>

      <section className="panel">
        <h3>Мои уведомления ({filters.length})</h3>
        {loading && <div className="state"><span className="skeleton" style={{ width: 120, height: 14 }} /></div>}
        {!loading && filters.length === 0 && (
          <div className="state">
            <div className="state__emoji">🔔</div>
            <div className="state__title">Уведомлений пока нет</div>
            <div className="state__text">
              Создайте первое — и мы напишем, когда появится подходящий авто
            </div>
          </div>
        )}
        <div className="filter-list">
          {filters.map((f) => (
            <div key={f.id} className="filter-item">
              <div className="filter-item__body">
                <div className="filter-item__name">{f.name ?? `Уведомление #${f.id}`}</div>
                <div className="filter-item__desc">
                  {describe(f.criteria)} · {f.notify_mode === "instant" ? "сообщим сразу" : "подборкой раз в день"}
                </div>
              </div>
              <button className="btn btn--danger" aria-label={`Удалить ${f.name ?? f.id}`} onClick={() => void remove(f.id)}>
                ✕
              </button>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

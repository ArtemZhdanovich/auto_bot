/** Фильтры (ТЗ п. 9.2.3): полный набор критериев, создание, удаление. */

import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Cell,
  Input,
  Placeholder,
  Section,
  Spinner,
  Switch,
} from "@telegram-apps/telegram-ui";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import type { Criteria, UserFilter } from "../api/types";

const FUELS: Array<[string, string]> = [
  ["", "Любое топливо"],
  ["petrol", "Бензин"],
  ["diesel", "Дизель"],
  ["hybrid", "Гибрид"],
  ["electric", "Электро"],
  ["lpg", "Газ / LPG"],
];
const TRANSMISSIONS: Array<[string, string]> = [
  ["", "Любая КПП"],
  ["auto", "Автомат"],
  ["manual", "Механика"],
  ["cvt", "Вариатор"],
  ["dct", "Робот / DCT"],
];
const BODIES: Array<[string, string]> = [
  ["", "Любой кузов"],
  ["sedan", "Седан"],
  ["hatchback", "Хэтчбек"],
  ["wagon", "Универсал"],
  ["suv", "Кроссовер / SUV"],
  ["coupe", "Купе"],
  ["van", "Минивэн"],
  ["pickup", "Пикап"],
];
const DRIVES: Array<[string, string]> = [
  ["", "Любой привод"],
  ["fwd", "Передний"],
  ["rwd", "Задний"],
  ["awd", "Полный"],
];

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
          ? "Достигнут лимит фильтров тарифа Free — освободите старый или оформите Pro"
          : "Не удалось создать фильтр, попробуйте позже",
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
      <Section header="Новый фильтр">
        <Input header="Название (необязательно)" value={form.name} onChange={(e) => set("name")(e.target.value)} placeholder="Семейный кроссовер" />
        <Input header="Марка" value={form.make} onChange={(e) => set("make")(e.target.value)} placeholder="BMW" />
        <Input header="Модель" value={form.model} onChange={(e) => set("model")(e.target.value)} placeholder="X5" />
        <div className="filter-pair">
          <Input header="Год от" inputMode="numeric" value={form.yearFrom} onChange={(e) => set("yearFrom")(onlyDigits(e.target.value))} placeholder="2019" />
          <Input header="Год до" inputMode="numeric" value={form.yearTo} onChange={(e) => set("yearTo")(onlyDigits(e.target.value))} placeholder="2024" />
        </div>
        <div className="filter-pair">
          <Input header="Цена $ от" inputMode="numeric" value={form.priceMin} onChange={(e) => set("priceMin")(onlyDigits(e.target.value))} placeholder="15000" />
          <Input header="Цена $ до" inputMode="numeric" value={form.priceMax} onChange={(e) => set("priceMax")(onlyDigits(e.target.value))} placeholder="40000" />
        </div>
        <Input header="Пробег, км — не более" inputMode="numeric" value={form.mileageMax} onChange={(e) => set("mileageMax")(onlyDigits(e.target.value))} placeholder="100000" />
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
        <Cell
          subtitle="Уведомления сразу, а не в дайджесте"
          after={
            <Switch
              checked={instant}
              onChange={(e) => setInstant((e.target as HTMLInputElement).checked)}
            />
          }
        >
          Instant-уведомления
        </Cell>
        {error && <div className="form-error">{error}</div>}
        <Button size="l" stretched loading={creating} onClick={() => void create()}>
          Создать фильтр
        </Button>
      </Section>

      <Section header={`Мои фильтры (${filters.length})`}>
        {loading && <Spinner size="m" />}
        {!loading && filters.length === 0 && (
          <Placeholder description="Создайте первый фильтр — под него придут уведомления" />
        )}
        {filters.map((f) => (
          <Cell
            key={f.id}
            subtitle={`${describe(f.criteria)} · ${f.notify_mode}`}
            after={
              <Button mode="outline" size="s" onClick={() => void remove(f.id)}>
                ✕
              </Button>
            }
          >
            {f.name ?? `Фильтр #${f.id}`}
          </Cell>
        ))}
      </Section>
    </div>
  );
}

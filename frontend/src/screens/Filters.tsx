/** Фильтры (ТЗ п. 9.2.3): список, создание, удаление. */

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
import type { UserFilter } from "../api/types";

export function FiltersScreen() {
  const [filters, setFilters] = useState<UserFilter[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [make, setMake] = useState("");
  const [priceMax, setPriceMax] = useState("");
  const [instant, setInstant] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setFilters(await api<UserFilter[]>("/filters"));
    setLoading(false);
  }, []);

  useEffect(() => {
    void reload().catch(() => setLoading(false));
  }, [reload]);

  const create = async () => {
    await api<UserFilter>("/filters", {
      method: "POST",
      body: JSON.stringify({
        name: name || null,
        criteria: {
          ...(make ? { make } : {}),
          ...(priceMax ? { price_max: Number(priceMax) } : {}),
        },
        notify_mode: instant ? "instant" : "digest",
      }),
    });
    haptic();
    setName("");
    setMake("");
    setPriceMax("");
    setInstant(false);
    await reload();
  };

  const remove = async (id: number) => {
    await api<void>(`/filters/${id}`, { method: "DELETE" });
    haptic();
    await reload();
  };

  return (
    <div style={{ padding: "12px", display: "flex", flexDirection: "column", gap: "12px" }}>
      <Section header="Новый фильтр">
        <Input header="Название" value={name} onChange={(e) => setName(e.target.value)} placeholder="Мой поиск" />
        <Input header="Марка" value={make} onChange={(e) => setMake(e.target.value)} placeholder="BMW" />
        <Input
          header="Цена, до"
          type="number"
          value={priceMax}
          onChange={(e) => setPriceMax(e.target.value)}
          placeholder="20000"
        />
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
        <Button size="l" stretched onClick={() => void create()}>
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
            subtitle={`${[f.criteria.make, f.criteria.price_max ? `до ${f.criteria.price_max}` : null]
              .filter(Boolean)
              .join(", ") || "все объявления"} · ${f.notify_mode}`}
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

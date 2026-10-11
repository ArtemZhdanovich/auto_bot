/** Лента объявлений (ТЗ п. 9.2.4): фильтры-чипсы, бесконечный скролл. */

import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Card, Cell, Chip, Button, Spinner, Placeholder } from "@telegram-apps/telegram-ui";

import { api } from "../api/client";
import type { Listing, ListingList } from "../api/types";

const PAGE_SIZE = 20;

interface FeedQuery {
  make?: string;
  price_max?: string;
  year_from?: string;
}

export function FeedScreen() {
  const [items, setItems] = useState<Listing[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState<FeedQuery>({});
  const offsetRef = useRef(0);
  const navigate = useNavigate();

  const load = useCallback(
    async (q: FeedQuery, reset: boolean) => {
      setLoading(true);
      const params = new URLSearchParams();
      if (q.make) params.set("make", q.make);
      if (q.price_max) params.set("price_max", q.price_max);
      if (q.year_from) params.set("year_from", q.year_from);
      params.set("limit", String(PAGE_SIZE));
      params.set("offset", String(reset ? 0 : offsetRef.current));
      const data = await api<ListingList>(`/listings?${params.toString()}`);
      setTotal(data.total);
      setItems((prev) => (reset ? data.items : [...prev, ...data.items]));
      offsetRef.current = reset ? data.items.length : offsetRef.current + data.items.length;
      setLoading(false);
    },
    [],
  );

  useEffect(() => {
    void load(query, true);
  }, [query, load]);

  const hasMore = items.length < total;

  return (
    <div style={{ padding: "12px", display: "flex", flexDirection: "column", gap: "8px" }}>
      <div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
        <Chip
          mode={query.make === "BMW" ? "elevated" : "outline"}
          onClick={() => setQuery((q) => ({ ...q, make: q.make === "BMW" ? undefined : "BMW" }))}
        >
          BMW
        </Chip>
        <Chip
          mode={query.make === "Audi" ? "elevated" : "outline"}
          onClick={() => setQuery((q) => ({ ...q, make: q.make === "Audi" ? undefined : "Audi" }))}
        >
          Audi
        </Chip>
        <Chip
          mode={query.price_max === "20000" ? "elevated" : "outline"}
          onClick={() =>
            setQuery((q) => ({ ...q, price_max: q.price_max === "20000" ? undefined : "20000" }))
          }
        >
          до $20 000
        </Chip>
      </div>

      {items.map((l) => (
        <Card key={l.id} onClick={() => navigate(`/listing/${l.id}`)} type="plain">
          <Cell
            subtitle={`${l.year ?? "—"} · ${l.mileage_km ? `${l.mileage_km.toLocaleString("ru")} км` : "пробег не указан"}`}
            description={`${l.source} · ${new Date(l.parsed_at).toLocaleDateString("ru")}`}
            after={
              l.price !== null ? (
                <b>
                  {l.price.toLocaleString("ru")} {l.currency ?? ""}
                </b>
              ) : (
                "—"
              )
            }
          >
            {l.make} {l.model}
          </Cell>
        </Card>
      ))}

      {loading && <Spinner size="m" />}
      {!loading && items.length === 0 && (
        <Placeholder header="Ничего не найдено" description="Попробуйте изменить фильтры" />
      )}
      {!loading && hasMore && (
        <Button mode="bezeled" size="l" onClick={() => void load(query, false)}>
          Ещё ({total - items.length})
        </Button>
      )}
    </div>
  );
}

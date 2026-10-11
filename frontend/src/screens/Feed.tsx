/** Лента объявлений (UI/UX ТЗ §2.2): динамические чипсы, бесконечный скролл. */

import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import { ListingCard } from "../components/ListingCard";
import { SkeletonList } from "../ui/Skeleton";
import { EmptyState, ErrorState } from "../ui/States";
import type { Listing, ListingList, UserFilter } from "../api/types";

const PAGE_SIZE = 20;

interface FeedQuery {
  make?: string;
  price_max?: string;
  year_from?: string;
}

/** Быстрые пресеты (ТЗ §2.2) + сохранённые фильтры пользователя. */
interface Chip {
  key: string;
  label: string;
  query: FeedQuery;
}

function buildChips(filters: UserFilter[]): Chip[] {
  const presets: Chip[] = [
    { key: "all", label: "Все", query: {} },
    { key: "p20k", label: "до $20 000", query: { price_max: "20000" } },
    { key: "y2022", label: "2022+", query: { year_from: "2022" } },
  ];
  const saved: Chip[] = filters.map((f) => ({
    key: `f${f.id}`,
    label: f.name ?? `Фильтр #${f.id}`,
    query: {
      make: f.criteria.make,
      price_max: f.criteria.price_max !== undefined ? String(f.criteria.price_max) : undefined,
      year_from: f.criteria.year_from !== undefined ? String(f.criteria.year_from) : undefined,
    },
  }));
  return [...presets, ...saved];
}

function sameQuery(a: FeedQuery, b: FeedQuery): boolean {
  return a.make === b.make && a.price_max === b.price_max && a.year_from === b.year_from;
}

export function FeedScreen() {
  const [items, setItems] = useState<Listing[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chips, setChips] = useState<Chip[]>([]);
  const [query, setQuery] = useState<FeedQuery>({});
  const offsetRef = useRef(0);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const queryRef = useRef(query);
  queryRef.current = query;

  // Динамические чипсы: пресеты + сохранённые фильтры (ТЗ §2.2)
  useEffect(() => {
    api<UserFilter[]>("/filters")
      .then((filters) => setChips(buildChips(filters)))
      .catch(() => setChips(buildChips([])));
  }, []);

  const load = useCallback(async (q: FeedQuery, reset: boolean) => {
    if (reset) setLoading(true);
    else setLoadingMore(true);
    setError(null);
    try {
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
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
      setLoadingMore(false);
    }
  }, []);

  useEffect(() => {
    void load(query, true);
  }, [query, load]);

  // Бесконечный скролл (ТЗ §2.2) вместо кнопки «Ещё»
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting && !loading && !loadingMore && !error) {
          if (items.length < total) {
            void load(queryRef.current, false);
          }
        }
      },
      { rootMargin: "400px" },
    );
    io.observe(el);
    return () => io.disconnect();
  }, [items.length, total, loading, loadingMore, error, load]);

  const activeChip = chips.find((c) => sameQuery(c.query, query))?.key ?? "";

  const retry = useCallback(() => {
    void load(queryRef.current, items.length === 0);
  }, [load, items.length]);

  return (
    <div>
      <div className="feed-header">
        <div className="feed-header__title">Лента</div>
        {!loading && !error && (
          <div className="feed-header__counter">
            {total.toLocaleString("ru")} авто · новые сверху
          </div>
        )}
        <div className="chip-row">
          {chips.map((c) => (
            <button
              key={c.key}
              className={c.key === activeChip ? "chip chip--active" : "chip"}
              onClick={() => setQuery(c.query)}
            >
              {c.label}
            </button>
          ))}
        </div>
      </div>

      <div className="feed-grid">
        {items.map((l) => (
          <ListingCard key={l.id} listing={l} />
        ))}

        {loading && <SkeletonList count={4} />}

        {!loading && error && items.length === 0 && (
          <ErrorState description="Не удалось загрузить ленту" onRetry={retry} />
        )}

        {!loading && !error && items.length === 0 && (
          <EmptyState
            title="Ничего не найдено"
            description="Попробуйте изменить фильтры"
          />
        )}
      </div>

      {/* Сентинел бесконечного скролла */}
      <div ref={sentinelRef} style={{ height: 1 }} />
      {loadingMore && (
        <div style={{ padding: 16 }}>
          <SkeletonList count={1} />
        </div>
      )}
      {!loading && !error && items.length >= total && items.length > 0 && (
        <div className="state" style={{ padding: 16 }}>
          Показаны все найденные варианты
        </div>
      )}
    </div>
  );
}


/** Лента объявлений (UI/UX ТЗ §2.2): чипсы, панель фильтров, бесконечный скролл. */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
} from "react";

import { api } from "../api/client";
import { useNavigationType } from "react-router-dom";
import { ListingCard } from "../components/ListingCard";
import { SkeletonList } from "../ui/Skeleton";
import { EmptyState, ErrorState } from "../ui/States";
import { KRW_PER_USD, digits } from "../lib/fx";
import type { Listing, ListingList, UserFilter } from "../api/types";

/** Ключи сортировки ленты (PLP-стандарт e-commerce). */
type SortKey = "new" | "price_asc" | "price_desc" | "mileage_asc" | "year_desc";

const SORTS: { key: SortKey; label: string; counter: string }[] = [
  { key: "new", label: "Новые", counter: "новые сверху" },
  { key: "price_asc", label: "Цена ↑", counter: "дешёвые сверху" },
  { key: "price_desc", label: "Цена ↓", counter: "дорогие сверху" },
  { key: "mileage_asc", label: "Пробег ↑", counter: "малый пробег" },
  { key: "year_desc", label: "Год ↓", counter: "свежие годы" },
];

const PAGE_SIZE = 20;

/**
 * Критерии ленты. Цены — в $ (как на экране фильтров); в БД цены хранятся
 * в валюте источника (воны), поэтому в запросе конвертируются (buildParams).
 */
interface FeedQuery {
  make?: string;
  model?: string;
  yearFrom?: string;
  yearTo?: string;
  priceMin?: string;
  priceMax?: string;
  mileageMax?: string;
  source?: string;
}

/** Быстрые пресеты (ТЗ §2.2) + сохранённые фильтры пользователя. */
interface Chip {
  key: string;
  label: string;
  query: FeedQuery;
}

const PRESETS: Chip[] = [
  { key: "all", label: "Все", query: {} },
  { key: "p10", label: "до $10 000", query: { priceMax: "10000" } },
  { key: "p10_20", label: "$10–20 тыс.", query: { priceMin: "10000", priceMax: "20000" } },
  { key: "p20_30", label: "$20–30 тыс.", query: { priceMin: "20000", priceMax: "30000" } },
  { key: "p30", label: "от $30 000", query: { priceMin: "30000" } },
  { key: "y2022", label: "2022+", query: { yearFrom: "2022" } },
  { key: "y18_21", label: "2018–2021", query: { yearFrom: "2018", yearTo: "2021" } },
  { key: "m60", label: "до 60 тыс. км", query: { mileageMax: "60000" } },
  { key: "m100", label: "до 100 тыс. км", query: { mileageMax: "100000" } },
];

function buildChips(filters: UserFilter[]): Chip[] {
  const saved: Chip[] = filters.map((f) => {
    const c = f.criteria;
    return {
      key: `f${f.id}`,
      label: f.name ?? `Фильтр #${f.id}`,
      query: {
        make: c.make,
        model: c.model,
        yearFrom: c.year_from !== undefined ? String(c.year_from) : undefined,
        yearTo: c.year_to !== undefined ? String(c.year_to) : undefined,
        priceMin: c.price_min !== undefined ? String(c.price_min) : undefined,
        priceMax: c.price_max !== undefined ? String(c.price_max) : undefined,
        mileageMax: c.mileage_max !== undefined ? String(c.mileage_max) : undefined,
      },
    };
  });
  return [...PRESETS, ...saved];
}

function sameQuery(a: FeedQuery, b: FeedQuery): boolean {
  return (
    a.make === b.make &&
    a.model === b.model &&
    a.yearFrom === b.yearFrom &&
    a.yearTo === b.yearTo &&
    a.priceMin === b.priceMin &&
    a.priceMax === b.priceMax &&
    a.mileageMax === b.mileageMax &&
    a.source === b.source
  );
}

const activeFields = (q: FeedQuery): number =>
  Object.values(q).filter((v) => v !== undefined && v !== "").length;

/** $ → воны (цены в БД хранятся в валюте источника — encar/kcar, KRW). */
const usdToKrw = (usd: string): string => String(Math.round(Number(usd) * KRW_PER_USD));

/**
 * Кэш ленты: возврат из карточки по «Назад» восстанавливает фильтры,
 * сортировку, список и позицию скролла (стандарт e-commerce каталога).
 */
interface FeedCache {
  query: FeedQuery;
  sort: SortKey;
  items: Listing[];
  total: number;
  offset: number;
  scrollY: number;
}
let feedCache: FeedCache | null = null;

/** Сброс кэша (для тестов и принудительного обновления). */
export function resetFeedCache(): void {
  feedCache = null;
}

export function FeedScreen() {
  const navType = useNavigationType(); // POP = назад/вперёд по истории
  const restore = useRef(navType === "POP" ? feedCache : null);
  const [items, setItems] = useState<Listing[]>(() => restore.current?.items ?? []);
  const [total, setTotal] = useState(() => restore.current?.total ?? 0);
  const [loading, setLoading] = useState(() => !restore.current);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [chips, setChips] = useState<Chip[]>([]);
  const [query, setQuery] = useState<FeedQuery>(() => restore.current?.query ?? {});
  const [sort, setSort] = useState<SortKey>(() => restore.current?.sort ?? "new");
  const [panelOpen, setPanelOpen] = useState(false);
  const [draft, setDraft] = useState<FeedQuery>({});
  const [showTop, setShowTop] = useState(false);
  const offsetRef = useRef(restore.current?.offset ?? 0);
  const sentinelRef = useRef<HTMLDivElement | null>(null);
  const queryRef = useRef(query);
  queryRef.current = query;
  const sortRef = useRef(sort);
  sortRef.current = sort;
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const totalRef = useRef(total);
  totalRef.current = total;
  const scrollYRef = useRef(restore.current?.scrollY ?? 0);

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
      if (q.model) params.set("model", q.model);
      if (q.yearFrom) params.set("year_from", q.yearFrom);
      if (q.yearTo) params.set("year_to", q.yearTo);
      if (q.priceMin) params.set("price_min", usdToKrw(q.priceMin));
      if (q.priceMax) params.set("price_max", usdToKrw(q.priceMax));
      if (q.mileageMax) params.set("mileage_max", q.mileageMax);
      if (q.source) params.set("source", q.source);
      params.set("sort", sortRef.current);
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
    // sort в deps: смена сортировки перезагружает ленту с первой страницы
  }, [sort]);

  useEffect(() => {
    if (restore.current) return; // возврат по «Назад» — лента уже восстановлена
    void load(query, true);
  }, [query, load]);

  // Возврат из карточки: восстановить позицию скролла после отрисовки карточек
  useEffect(() => {
    if (!restore.current) return;
    const y = restore.current.scrollY;
    restore.current = null; // дальше — обычная логика
    const t = window.setTimeout(() => window.scrollTo(0, y), 50);
    return () => window.clearTimeout(t);
  }, []);

  // Скролл: позиция для кэша + кнопка «Наверх» после пары экранов
  useEffect(() => {
    const onScroll = () => {
      scrollYRef.current = window.scrollY;
      setShowTop(window.scrollY > 1600);
    };
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      window.removeEventListener("scroll", onScroll);
      // Сохраняем ленту на выходе — «Назад» из карточки вернёт как было
      feedCache = {
        query: queryRef.current,
        sort: sortRef.current,
        items: itemsRef.current,
        total: totalRef.current,
        offset: offsetRef.current,
        scrollY: scrollYRef.current,
      };
    };
  }, []);

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
  const activeCount = activeFields(query);

  const retry = useCallback(() => {
    void load(queryRef.current, items.length === 0);
  }, [load, items.length]);

  const togglePanel = useCallback(() => {
    setPanelOpen((v) => {
      if (!v) setDraft(queryRef.current); // открыть с текущими значениями
      return !v;
    });
  }, []);

  const setField =
    (key: keyof FeedQuery, numeric = false) =>
    (e: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      const raw = numeric ? digits(e.target.value) : e.target.value;
      setDraft((d) => ({ ...d, [key]: raw || undefined }));
    };

  const applyPanel = useCallback(() => {
    setQuery(draft);
    setPanelOpen(false);
  }, [draft]);

  const resetPanel = useCallback(() => {
    setDraft({});
    setQuery({});
  }, []);

  return (
    <div>
      <div className="feed-header">
        <div className="feed-header__title">Лента</div>
        {!loading && !error && (
          <div className="feed-header__counter">
            {total.toLocaleString("ru")} авто ·{" "}
            {SORTS.find((s) => s.key === sort)?.counter ?? "новые сверху"}
          </div>
        )}
        <div className="feed-subbar">
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
          <div className="feed-subbar__controls">
            <select
              className="feed-sort"
              value={sort}
              onChange={(e) => setSort(e.target.value as SortKey)}
              aria-label="Сортировка"
            >
              {SORTS.map((s) => (
                <option key={s.key} value={s.key}>
                  {s.label}
                </option>
              ))}
            </select>
            <button
              className={panelOpen || activeCount > 0 ? "feed-toggle feed-toggle--on" : "feed-toggle"}
              onClick={togglePanel}
              aria-expanded={panelOpen}
            >
              Фильтры{activeCount > 0 ? ` · ${activeCount}` : ""}
            </button>
          </div>
        </div>

        {panelOpen && (
          <div className="feed-panel">
            <label className="feed-panel__field">
              <span>Марка</span>
              <input
                value={draft.make ?? ""}
                onChange={setField("make")}
                placeholder="Hyundai"
              />
            </label>
            <label className="feed-panel__field">
              <span>Модель</span>
              <input
                value={draft.model ?? ""}
                onChange={setField("model")}
                placeholder="Sonata"
              />
            </label>
            <label className="feed-panel__field">
              <span>Год от</span>
              <input
                inputMode="numeric"
                value={draft.yearFrom ?? ""}
                onChange={setField("yearFrom", true)}
                placeholder="2019"
              />
            </label>
            <label className="feed-panel__field">
              <span>Год до</span>
              <input
                inputMode="numeric"
                value={draft.yearTo ?? ""}
                onChange={setField("yearTo", true)}
                placeholder="2024"
              />
            </label>
            <label className="feed-panel__field">
              <span>Цена $ от</span>
              <input
                inputMode="numeric"
                value={draft.priceMin ?? ""}
                onChange={setField("priceMin", true)}
                placeholder="5 000"
              />
            </label>
            <label className="feed-panel__field">
              <span>Цена $ до</span>
              <input
                inputMode="numeric"
                value={draft.priceMax ?? ""}
                onChange={setField("priceMax", true)}
                placeholder="30 000"
              />
            </label>
            <label className="feed-panel__field">
              <span>Пробег, км — не более</span>
              <input
                inputMode="numeric"
                value={draft.mileageMax ?? ""}
                onChange={setField("mileageMax", true)}
                placeholder="100 000"
              />
            </label>
            <label className="feed-panel__field">
              <span>Источник</span>
              <select value={draft.source ?? ""} onChange={setField("source")}>
                <option value="">Любой</option>
                <option value="encar">Encar</option>
                <option value="kcar">Kcar</option>
              </select>
            </label>
            <div className="feed-panel__actions">
              <button className="feed-panel__apply" onClick={applyPanel}>
                Применить
              </button>
              <button className="feed-panel__reset" onClick={resetPanel}>
                Сбросить
              </button>
            </div>
          </div>
        )}
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
        <div className="feed-more">
          <SkeletonList count={1} />
        </div>
      )}

      {/* Ошибка подгрузки: лента не должна молча останавливаться */}
      {!loading && !loadingMore && error && items.length > 0 && (
        <div className="feed-more feed-more--error">
          <span>Не удалось загрузить следующие объявления</span>
          <button
            className="feed-more__btn"
            onClick={() => void load(query, false)}
          >
            Повторить
          </button>
        </div>
      )}

      {/* Ручная подгрузка — фолбэк, если IntersectionObserver не сработал */}
      {!loading && !loadingMore && !error && items.length < total && (
        <div className="feed-more">
          <button
            className="feed-more__btn"
            onClick={() => void load(query, false)}
          >
            Показать ещё
          </button>
          <span className="feed-more__hint">
            {items.length} из {total.toLocaleString("ru")}
          </span>
        </div>
      )}

      {!loading && !error && items.length >= total && items.length > 0 && (
        <div className="state" style={{ padding: 16 }}>
          Показаны все найденные варианты
        </div>
      )}

      {/* Кнопка «Наверх» — после пары экранов прокрутки */}
      {showTop && (
        <button
          className="to-top"
          aria-label="Наверх"
          onClick={() => window.scrollTo({ top: 0, behavior: "smooth" })}
        >
          ↑
        </button>
      )}
    </div>
  );
}


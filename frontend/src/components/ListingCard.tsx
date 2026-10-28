/**
 * Карточка объявления в ленте — по спеке редизайна (01-ux-audit §1.2/§3.1,
 * 02-design-system §7.3): фото 16:9 → бейджи → модель → год·пробег·страна·
 * источник (текстом, F9) → цена $ + ≈₽ (F6) → 3 спека (F8).
 *
 * Аудит 01-ux-audit-ia.md:
 * - F6: две цены — $ (Title M 18/700 tabular-nums) + ≈ валюта региона
 *   (caption); обе уже считает priceChain (lib/fx.ts);
 * - F7: бейдж «🧾 Чек» по полю has_inspection (читаем оборонительно —
 *   поле новое и появится в ответе после деплоя бэкенда);
 * - F8: максимум 3 спека, VIN/цвет/кузов — на PDP;
 * - F9: ссылка encar.com ↗ убрана — источник текстом в метастроке,
 *   выход к источнику только осознанно, из PDP.
 *
 * Тап по всей карточке ведёт на /listing/:id; у карточки aria-label
 * «модель, год, цена» для скринридеров (03-content-a11y §5.2).
 *
 * Мини-карусель фото (только ПК ≥768px, media-запросом; на мобиле —
 * одно фото, как раньше): горизонтальный скролл со snap, drag мышью
 * (смещение >DRAG_THRESHOLD px подавляет клик-переход в карточку),
 * максимум CARD_MAX_PHOTOS фото («+N фото» на последнем слайде, остальные
 * — в PDP) и точки-индикаторы внизу-центру, синхронные со скроллом.
 */

import { memo, useCallback, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Car, Heart } from "lucide-react";

import { api } from "../api/client";
import { haptic, isGuest } from "../api/auth";
import { getRegion, priceChain } from "../lib/fx";
import { fuelLabel, transmissionLabel, driveLabel } from "../lib/specs";
import { useToast } from "../ui/Toast";
import { useLoginPrompt } from "../ui/LoginPrompt";
import type { Listing } from "../api/types";

interface Props {
  listing: Listing;
  favorited?: boolean;
}

/** Страна и имя источника в метастроке (F9): без флагов — источник это
 *  каталог, не география (02-design-system §7.6). */
const SOURCE_META: Record<string, { country: string; name: string }> = {
  encar: { country: "Корея", name: "Encar" },
  kcar: { country: "Корея", name: "KCar" },
};

/** Дата парсинга в формате метастроки: «21.10». */
const shortDate = (iso: string): string => {
  const d = new Date(iso);
  return Number.isNaN(d.getTime())
    ? ""
    : d.toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit" });
};

/** Лимит фото в карусели карточки ленты (задача владельца): длинные
 *  галереи листать в ленте нечего — всё остальное смотрим в PDP. */
const CARD_MAX_PHOTOS = 5;

/** Порог (px) смещения мыши, после которого жест считается перетаскиванием
 *  карусели, а не кликом: тогда переход в карточку подавляем. */
const DRAG_THRESHOLD = 6;

/** prefers-reduced-motion (§6.2.5): плавный скролл точек отключаем. */
const reducedMotion = () =>
  window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;

export const ListingCard = memo(function ListingCard({ listing, favorited = false }: Props) {
  const navigate = useNavigate();
  const toast = useToast();
  const loginPrompt = useLoginPrompt();
  const [fav, setFav] = useState(favorited);
  // Состояния фото: loading (skeleton) → loaded | failed (silhouette)
  const [photoLoaded, setPhotoLoaded] = useState(false);
  const [photoFailed, setPhotoFailed] = useState(false);
  const photo = listing.images[0];

  // Мини-карусель (ПК): активный слайд по скроллу + drag-to-scroll мышью
  const trackRef = useRef<HTMLDivElement | null>(null);
  const [slideIdx, setSlideIdx] = useState(0);
  const dragRef = useRef<{ startX: number; startScroll: number; moved: boolean } | null>(null);
  // Клик сразу после перетаскивания — не «открыть карточку», а завершение жеста
  const suppressClickRef = useRef(false);
  // Лимит карусели: максимум 5 фото, на последнем — «+N фото» (остальные в PDP)
  const slides = listing.images.slice(0, CARD_MAX_PHOTOS);
  const restPhotos = listing.images.length - slides.length;

  // F6: обе цены считает priceChain — $ и ≈ валюта региона
  const chain = priceChain(listing.price, listing.currency, getRegion());
  // Единый тайтл: без «null undefined», если у источника нет марки/модели
  const title = `${listing.make ?? ""} ${listing.model ?? ""}`.trim() || "Без названия";

  // F9: источник — текстом в метастроке, а не ссылкой
  const meta = SOURCE_META[listing.source.toLowerCase()];
  const metaParts = [
    listing.year ? String(listing.year) : "",
    listing.mileage_km != null
      ? `${listing.mileage_km.toLocaleString("ru-RU")} км`
      : "",
    meta?.country ?? "",
    meta?.name ?? listing.source,
    listing.parsed_at ? shortDate(listing.parsed_at) : "",
  ].filter(Boolean);

  // Бейджи отслеживания: снижение цены и исчезновение (фаза 4) + чек (F7)
  const hasInspection =
    (listing as { has_inspection?: boolean }).has_inspection ?? false;
  // Внутренний рынок (06 §4): владелец лота — салон/частник; owner_type нет
  // (старый ответ) — фолбэк на плоский has_market («🏪 Рынок»)
  const ownerType = listing.owner_type ?? null;
  const hasMarket =
    (listing as { has_market?: boolean }).has_market ?? false;
  const dropped =
    listing.first_price != null &&
    listing.price != null &&
    listing.price < listing.first_price;
  const dropPct =
    dropped && listing.first_price
      ? Math.round((1 - (listing.price as number) / listing.first_price) * 100)
      : 0;
  const gone = listing.is_active === false;

  // F8: максимум 3 спека (объём · топливо · КПП, далее привод);
  // полный набор — на PDP, VIN из ленты убран
  const specParts = [
    listing.engine_volume ? `${listing.engine_volume.toFixed(1)} л` : "",
    fuelLabel(listing.fuel) ?? "",
    transmissionLabel(listing.transmission) ?? "",
    driveLabel(listing.drive) ?? "",
  ]
    .filter(Boolean)
    .slice(0, 3);

  // aria-label карточки для скринридеров: модель, год, цена
  const cardLabel = `${title}${listing.year ? `, ${listing.year}` : ""}${
    chain.usd ? `, ${chain.usd}` : ", цена не указана"
  }`;

  const onOpen = useCallback(() => {
    // Перетаскивание карусели не должно открывать карточку (drag ≠ click):
    // сместили >6px — «съедаем» следующий клик как завершение жеста
    if (suppressClickRef.current) {
      suppressClickRef.current = false;
      return;
    }
    haptic();
    navigate(`/listing/${listing.id}`);
  }, [navigate, listing.id]);

  // ---------- Мини-карусель (ПК ≥768): скролл + точки + drag мышью ----------

  /** Точки кликабельны: прокрутка к слайду i (auto при reduced-motion). */
  const goToSlide = useCallback((i: number) => {
    const el = trackRef.current;
    setSlideIdx(i);
    if (!el) return;
    const left = i * el.clientWidth;
    const behavior: ScrollBehavior = reducedMotion() ? "auto" : "smooth";
    if (typeof el.scrollTo === "function") el.scrollTo({ left, behavior });
    else el.scrollLeft = left; // старые WebView / jsdom без element.scrollTo
  }, []);

  /** Активная точка = видимый слайд: index = round(scrollLeft / ширина). */
  const onTrackScroll = useCallback(
    (e: React.UIEvent<HTMLDivElement>) => {
      const el = e.currentTarget;
      const w = el.clientWidth || 1;
      const i = Math.min(Math.max(Math.round(el.scrollLeft / w), 0), slides.length - 1);
      setSlideIdx(i);
    },
    [slides.length],
  );

  /** Drag-to-scroll мышью (ПК): touch не трогаем — там нативный свайп
   *  страницы. Захват указателя держит события на треке за его пределами. */
  const onDragStart = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    if (e.pointerType && e.pointerType !== "mouse") return; // только мышь
    if (e.button !== 0) return; // основная кнопка
    const el = trackRef.current;
    if (!el) return;
    dragRef.current = { startX: e.clientX, startScroll: el.scrollLeft, moved: false };
    el.setPointerCapture?.(e.pointerId);
  }, []);

  const onDragMove = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    const el = trackRef.current;
    if (!d || !el) return;
    const dx = e.clientX - d.startX;
    // Мелкая дрожь ≤6px — ещё клик, а не жест: не подавляем и не тянем
    if (!d.moved && Math.abs(dx) <= DRAG_THRESHOLD) return;
    d.moved = true;
    el.scrollLeft = d.startScroll - dx; // тянем ленту за курсор
  }, []);

  const onDragEnd = useCallback((e: React.PointerEvent<HTMLDivElement>) => {
    const d = dragRef.current;
    if (!d) return;
    dragRef.current = null;
    trackRef.current?.releasePointerCapture?.(e.pointerId);
    // Был настоящий сдвиг — клик, который придёт следом, подавляем
    if (d.moved) suppressClickRef.current = true;
  }, []);

  const onDragCancel = useCallback(() => {
    // Браузер перехватил жест (скролл/прерывание) — ничего не подавляем
    dragRef.current = null;
  }, []);

  const onToggleFav = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      haptic("medium");
      // Гостю избранное недоступно: модалка с просьбой войти (ТЗ п. 9.1)
      if (isGuest()) {
        loginPrompt.show("Войдите, чтобы сохранять машины в избранное.");
        return;
      }
      try {
        const r = await api<{ favorited: boolean }>(`/listings/${listing.id}/favorite`, {
          method: "POST",
        });
        setFav(r.favorited);
        toast.show(r.favorited ? "❤️ Добавлено в избранное" : "Убрано из избранного");
      } catch {
        toast.show("Не удалось изменить избранное", { error: true });
      }
    },
    [listing.id, toast, loginPrompt],
  );

  return (
    <article
      className={gone ? "lcard lcard--gone" : "lcard"}
      role="button"
      tabIndex={0}
      aria-label={cardLabel}
      onClick={onOpen}
      onKeyDown={(e) => {
        // Enter и Space — карточка доступна с клавиатуры (03 §5.2)
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          onOpen();
        }
      }}
    >
      <div className="lcard__media">
        {photo && !photoFailed ? (
          <>
            {/* Мини-карусель: на мобиле <768px CSS оставляет первый слайд,
                на ПК — горизонтальный скролл со snap + drag мышью */}
            <div
              ref={trackRef}
              className="lcarousel"
              role="group"
              aria-roledescription="карусель"
              aria-label={`Фото ${slideIdx + 1} из ${slides.length}`}
              onScroll={onTrackScroll}
              onPointerDown={onDragStart}
              onPointerMove={onDragMove}
              onPointerUp={onDragEnd}
              onPointerCancel={onDragCancel}
            >
              {slides.map((src, i) => (
                <div className="lcarousel__slide" key={i}>
                  <img
                    className="lcard__img"
                    src={src}
                    alt={i === 0 ? title : `${title} — фото ${i + 1}`}
                    loading="lazy"
                    decoding="async"
                    onLoad={i === 0 ? () => setPhotoLoaded(true) : undefined}
                    onError={i === 0 ? () => setPhotoFailed(true) : undefined}
                  />
                  {/* Лимит 5 фото: на последнем слайде — «+N фото», клик —
                      как обычно в карточку, все фото смотрим в PDP */}
                  {i === slides.length - 1 && restPhotos > 0 && (
                    <span className="lcarousel__more">
                      +{restPhotos} фото
                    </span>
                  )}
                </div>
              ))}
            </div>
            {!photoLoaded && <span className="lcard__img-skel skeleton" aria-hidden="true" />}
            {/* Точки-индикаторы (ПК): внизу по центру, клик — scrollTo слайда */}
            {slides.length > 1 && (
              <div className="lcarousel__dots">
                {slides.map((_, i) => (
                  <button
                    key={i}
                    type="button"
                    className={
                      i === slideIdx
                        ? "lcarousel__dot lcarousel__dot--active"
                        : "lcarousel__dot"
                    }
                    aria-label={`Фото ${i + 1}`}
                    aria-current={i === slideIdx ? "true" : undefined}
                    onClick={(e) => {
                      // точка — управление каруселью, не переход в карточку
                      e.stopPropagation();
                      goToSlide(i);
                    }}
                  />
                ))}
              </div>
            )}
          </>
        ) : (
          <div className="lcard__ph" role="img" aria-label="Фото недоступно">
            <Car size={40} strokeWidth={1.25} aria-hidden="true" />
          </div>
        )}
        <span className="lcard__badges">
          {gone && <span className="lcard__badge lcard__badge--gone">🔴 Исчезло</span>}
          {dropped && <span className="lcard__badge lcard__badge--drop">⬇️ −{dropPct}%</span>}
          {hasInspection && <span className="lcard__badge lcard__badge--check">🧾 Чек</span>}
          {/* Рынок владельцев (06 §4): уточнённый бейдж по owner_type —
              «🏪 Салон» (партнёр) / «👤 Частник» (пользователь); плоский
              has_market — фолбэк на переходный период бэкенда */}
          {ownerType === "partner" && (
            <span className="lcard__badge lcard__badge--market">🏪 Салон</span>
          )}
          {ownerType === "user" && (
            <span className="lcard__badge lcard__badge--market">👤 Частник</span>
          )}
          {!ownerType && hasMarket && (
            <span className="lcard__badge lcard__badge--market">🏪 Рынок</span>
          )}
        </span>
        <button
          className="lcard__fav"
          onClick={(e) => void onToggleFav(e)}
          aria-label={fav ? "Убрать из избранного" : "В избранное"}
          aria-pressed={fav}
        >
          <Heart size={18} fill={fav ? "currentColor" : "none"} aria-hidden="true" />
        </button>
        {listing.images.length > 1 && (
          <span className="lcard__count">{listing.images.length}</span>
        )}
      </div>
      <div className="lcard__body">
        {/* Модель: Title S 16/600, ellipsis максимум в 2 строки (§7.3) —
            длинные корейские названия не распирают карточку и ряд сетки */}
        <h3 className="lcard__title lcard__title--clamp">{title}</h3>
        <div className="lcard__meta">{metaParts.join(" · ")}</div>
        {/* Цена: Title M 18/700 tabular-nums (§3.3) + вторая строка ≈ ₽ */}
        <div className="lcard__price tabular">{chain.usd ?? "—"}</div>
        {chain.usd && chain.local && (
          <div className="lcard__price-alt tabular">≈ {chain.local}</div>
        )}
        {specParts.length > 0 && (
          <div className="lcard__specs">{specParts.join(" · ")}</div>
        )}
      </div>
    </article>
  );
});

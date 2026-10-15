/** Карточка объявления (UI/UX ТЗ §2.3): галерея, характеристики, избранное. */

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Placeholder, Section, Cell } from "@telegram-apps/telegram-ui";
import { Heart, ExternalLink, Truck } from "lucide-react";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import { useToast } from "../ui/Toast";
import { SkeletonCard } from "../ui/Skeleton";
import { ErrorState } from "../ui/States";
import type { Listing } from "../api/types";
import { toUsd } from "../lib/fx";

export function ListingScreen() {
  const { id } = useParams<{ id: string }>();
  const [listing, setListing] = useState<Listing | null>(null);
  const [favorited, setFavorited] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();
  const toast = useToast();

  useEffect(() => {
    api<Listing>(`/listings/${id}`)
      .then(setListing)
      .catch((e: Error) => setError(e.message));
    api<{ favorited: boolean }>(`/favorites/${id}`)
      .then((r) => setFavorited(r.favorited))
      .catch(() => undefined); // вне Telegram — без избранного
  }, [id]);

  const toggleFavorite = async () => {
    haptic("medium");
    try {
      const r = await api<{ favorited: boolean }>(`/listings/${id}/favorite`, {
        method: "POST",
      });
      setFavorited(r.favorited);
      toast.show(r.favorited ? "❤️ Добавлено в избранное" : "Убрано из избранного");
    } catch {
      toast.show("Не удалось изменить избранное", { error: true });
    }
  };

  if (error) {
    return <ErrorState description={error} onRetry={() => navigate(-1)} />;
  }
  if (!listing) {
    return (
      <div style={{ padding: 12 }}>
        <SkeletonCard />
        <SkeletonCard />
      </div>
    );
  }

  const title = `${listing.make ?? ""} ${listing.model ?? ""}`.trim();
  // KRW/EUR → USD по фикс-курсу MVP (как в боте), иначе в калькулятор уйдут воны
  const usd = toUsd(listing.price, listing.currency);

  return (
    <div style={{ padding: "12px", display: "flex", flexDirection: "column", gap: "12px" }}>
      {/* Галерея: свайп + счётчик (ТЗ §2.3) */}
      {listing.images.length > 0 ? (
        <Gallery images={listing.images} alt={title} />
      ) : (
        <Placeholder description="Фото не загружены" />
      )}

      <Section header={title}>
        <Cell
          after={
            <b>
              {listing.price
                ? `${listing.price.toLocaleString("ru")} ${listing.currency ?? ""}`
                : "—"}
            </b>
          }
        >
          Цена
        </Cell>
        <Cell after={listing.year ?? "—"}>Год</Cell>
        <Cell after={listing.mileage_km ? `${listing.mileage_km.toLocaleString("ru")} км` : "—"}>
          Пробег
        </Cell>
        {usd != null && (listing.currency ?? "").toUpperCase() !== "USD" && (
          <Cell after={<b>≈ {usd.toLocaleString("ru")} $</b>}>По курсу (оценка)</Cell>
        )}
        <Cell after={listing.vin ?? "не указан"}>VIN</Cell>
        <Cell after={listing.source}>Источник</Cell>
      </Section>

      {/* Название с источника (PLAN.md, фаза 5): перевод, иначе оригинал */}
      {(listing.title_ru ?? listing.title_raw) && (
        <Section>
          <Cell after={<b>{listing.title_ru ?? listing.title_raw}</b>}>
            Название
          </Cell>
        </Section>
      )}

      {/* История цен (PLAN.md, фаза 4): точки listing_price_history, новые сверху */}
      {listing.price_history && listing.price_history.length > 1 && (
        <Section header="История цен">
          {[...listing.price_history].reverse().map((p, i) => (
            <Cell
              key={`${p.recorded_at}-${i}`}
              after={
                <b>
                  {p.price.toLocaleString("ru")} {p.currency ?? ""}
                </b>
              }
            >
              {new Date(p.recorded_at).toLocaleDateString("ru")}
              {i === 0 ? " · текущая" : ""}
            </Cell>
          ))}
        </Section>
      )}
      {listing.is_active === false && (
        <Placeholder
          header="🔴 Исчезло с продажи"
          description="Объявления больше нет на источнике"
        />
      )}

      <Button
        size="l"
        mode={favorited ? "filled" : "outline"}
        before={<Heart size={18} fill={favorited ? "currentColor" : "none"} />}
        onClick={() => void toggleFavorite()}
      >
        {favorited ? "В избранном" : "В избранное"}
      </Button>
      <Button
        size="l"
        before={<Truck size={18} />}
        onClick={() => {
          haptic();
          navigate(
            `/calc?${usd != null ? `price=${usd}&` : ""}year=${listing.year ?? ""}`,
          );
        }}
      >
        Доставка и растаможка
      </Button>
      <Button
        size="l"
        before={<ExternalLink size={18} />}
        onClick={() => {
          haptic();
          window.open(listing.url, "_blank");
        }}
      >
        Открыть у источника
      </Button>
      <Button mode="plain" size="l" onClick={() => navigate(-1)}>
        Назад
      </Button>
    </div>
  );
}

function Gallery({ images, alt }: { images: string[]; alt: string }) {
  const [idx, setIdx] = useState(0);
  return (
    <div className="gallery">
      <div
        className="gallery__track"
        onScroll={(e) => {
          const el = e.currentTarget;
          setIdx(Math.round(el.scrollLeft / el.clientWidth));
        }}
      >
        {images.map((src, i) => (
          <img key={i} className="gallery__slide" src={src} alt={`${alt} — фото ${i + 1}`} loading="lazy" />
        ))}
      </div>
      <span className="gallery__counter">
        {idx + 1} / {images.length}
      </span>
    </div>
  );
}


/** Карточка объявления (ТЗ п. 9.2.5). */

import { useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Button, Placeholder, Section, Cell, Spinner } from "@telegram-apps/telegram-ui";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import type { Listing } from "../api/types";

export function ListingScreen() {
  const { id } = useParams<{ id: string }>();
  const [listing, setListing] = useState<Listing | null>(null);
  const [favorited, setFavorited] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    api<Listing>(`/listings/${id}`)
      .then(setListing)
      .catch((e: Error) => setError(e.message));
    api<{ favorited: boolean }>(`/favorites/${id}`)
      .then((r) => setFavorited(r.favorited))
      .catch(() => undefined); // вне Telegram — без избранного
  }, [id]);

  const toggleFavorite = async () => {
    const r = await api<{ favorited: boolean }>(`/listings/${id}/favorite`, {
      method: "POST",
    });
    setFavorited(r.favorited);
    haptic();
  };

  if (error) {
    return <Placeholder header="Ошибка" description={error} />;
  }
  if (!listing) {
    return <Spinner size="l" />;
  }

  return (
    <div style={{ padding: "12px", display: "flex", flexDirection: "column", gap: "12px" }}>
      {listing.images.length > 0 && (
        <img
          src={listing.images[0]}
          alt={`${listing.make} ${listing.model}`}
          style={{ width: "100%", borderRadius: 12, maxHeight: 260, objectFit: "cover" }}
        />
      )}
      <Section header={`${listing.make ?? ""} ${listing.model ?? ""}`.trim()}>
        <Cell after={<b>{listing.price ? `${listing.price.toLocaleString("ru")} ${listing.currency ?? ""}` : "—"}</b>}>
          Цена
        </Cell>
        <Cell>{listing.year ?? "—"}</Cell>
        <Cell subtitle="Пробег">
          {listing.mileage_km ? `${listing.mileage_km.toLocaleString("ru")} км` : "—"}
        </Cell>
        <Cell subtitle="VIN">{listing.vin ?? "не указан"}</Cell>
        <Cell subtitle="Источник">{listing.source}</Cell>
      </Section>
      <Button
        size="l"
        mode={favorited ? "filled" : "outline"}
        onClick={toggleFavorite}
      >
        {favorited ? "❤️ В избранном" : "🤍 В избранное"}
      </Button>
      <Button
        size="l"
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

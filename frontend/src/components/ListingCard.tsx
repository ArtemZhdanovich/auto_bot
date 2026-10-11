/** Карточка объявления (UI/UX ТЗ §2.2): фото, цена, избранное, haptic. */

import { memo, useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Heart, ImageOff } from "lucide-react";

import { api } from "../api/client";
import { haptic } from "../api/auth";
import { useToast } from "../ui/Toast";
import type { Listing } from "../api/types";

interface Props {
  listing: Listing;
  favorited?: boolean;
}

export const ListingCard = memo(function ListingCard({ listing, favorited = false }: Props) {
  const navigate = useNavigate();
  const toast = useToast();
  const [fav, setFav] = useState(favorited);
  const [photoFailed, setPhotoFailed] = useState(false);
  const photo = listing.images[0];

  const onOpen = useCallback(() => {
    haptic();
    navigate(`/listing/${listing.id}`);
  }, [navigate, listing.id]);

  const onToggleFav = useCallback(
    async (e: React.MouseEvent) => {
      e.stopPropagation();
      haptic("medium");
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
    [listing.id, toast],
  );

  return (
    <div
      className="listing-card"
      role="button"
      tabIndex={0}
      onClick={onOpen}
      onKeyDown={(e) => e.key === "Enter" && onOpen()}
    >
      <div className="listing-card__photo-wrap">
        {photo && !photoFailed ? (
          <img
            className="listing-card__photo"
            src={photo}
            alt={`${listing.make ?? ""} ${listing.model ?? ""}`.trim()}
            loading="lazy"
            onError={() => setPhotoFailed(true)}
          />
        ) : (
          <div
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              height: "100%",
              color: "var(--text-secondary)",
            }}
          >
            <ImageOff size={32} />
          </div>
        )}
        <button className="listing-card__fav" onClick={(e) => void onToggleFav(e)} aria-label="В избранное">
          <Heart size={18} fill={fav ? "currentColor" : "none"} />
        </button>
        {listing.images.length > 1 && (
          <span className="listing-card__photos-count">{listing.images.length}</span>
        )}
      </div>
      <div className="listing-card__body">
        <div className="listing-card__title">
          {listing.make} {listing.model}
          {listing.year ? `, ${listing.year}` : ""}
        </div>
        <div className="listing-card__price">
          {listing.price !== null
            ? `${listing.price.toLocaleString("ru")} ${listing.currency ?? ""}`
            : "—"}
        </div>
        <div className="listing-card__meta">
          {listing.mileage_km
            ? `${listing.mileage_km.toLocaleString("ru")} км · `
            : ""}
          {[listing.source, listing.parsed_at && new Date(listing.parsed_at).toLocaleDateString("ru")]
            .filter(Boolean)
            .join(" · ")}
        </div>
      </div>
    </div>
  );
});

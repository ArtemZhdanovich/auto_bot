/** Экран «Избранное» (этап 6, ТЗ п. 9.2.4: toggle + список). */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { isGuest } from "../api/auth";
import { useLoginPrompt } from "../ui/LoginPrompt";
import { SkeletonCard } from "../ui/Skeleton";
import type { Listing } from "../api/types";

export function FavoritesScreen() {
  const [items, setItems] = useState<Listing[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [guest] = useState(() => isGuest());
  const loginPrompt = useLoginPrompt();
  const navigate = useNavigate();

  // Гость: раздел персональный — сразу просим войти (модалка + гейт экрана)
  useEffect(() => {
    if (guest) {
      loginPrompt.show("Избранное доступно после входа — так машины сохраняются за вашим аккаунтом.");
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [guest]);

  const load = useCallback(() => {
    api<Listing[]>("/favorites")
      .then(setItems)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    if (!guest) load();
  }, [guest, load]);

  const unfavorite = async (id: number) => {
    await api(`/listings/${id}/favorite`, { method: "DELETE" });
    setItems((prev) => prev?.filter((x) => x.id !== id) ?? null);
  };

  if (guest) {
    return (
      <div className="state">
        <div className="state__emoji">🔒</div>
        <div className="state__title">Нужен вход</div>
        <div className="state__text">
          Избранное доступно после входа через Telegram или по email — так машины
          сохраняются за вашим аккаунтом.
        </div>
        <button className="btn btn--primary" style={{ marginTop: 12 }} onClick={() => navigate("/login")}>
          Войти
        </button>
      </div>
    );
  }
  if (error) {
    return (
      <div className="state">
        <div className="state__emoji">🔒</div>
        <div className="state__title">Нужна авторизация</div>
        <div className="state__text">Избранное доступно после входа через Telegram</div>
      </div>
    );
  }
  if (items === null) {
    return (
      <div className="screen">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    );
  }
  if (items.length === 0) {
    return (
      <div className="state">
        <div className="state__emoji">🤍</div>
        <div className="state__title">Пока пусто</div>
        <div className="state__text">Откройте объявление в ленте и нажмите «В избранное»</div>
        <button className="btn btn--primary" style={{ marginTop: 12 }} onClick={() => navigate("/")}>
          К ленте
        </button>
      </div>
    );
  }

  return (
    <div className="screen">
      <section className="panel">
        <h3>Избранное: {items.length}</h3>
        <div className="fav-list">
          {items.map((l) => {
            // Бейджи отслеживания (PLAN.md, фаза 4): ⬇️ цена снизилась, 🔴 исчезло
            const dropped =
              l.first_price != null && l.price != null && l.price < l.first_price;
            const badge = l.is_active === false ? "🔴 " : dropped ? "⬇️ " : "";
            const oldPrice = dropped && l.first_price ? ` (было ${l.first_price.toLocaleString("ru")})` : "";
            return (
              <div
                key={l.id}
                className="filter-item"
                role="button"
                tabIndex={0}
                onClick={() => navigate(`/listing/${l.id}`)}
                onKeyDown={(e) => e.key === "Enter" && navigate(`/listing/${l.id}`)}
              >
                <div className="filter-item__body">
                  <div className="filter-item__name">
                    {`${badge}${l.make ?? ""} ${l.model ?? ""}`.trim() || "Объявление"}
                  </div>
                  <div className="filter-item__desc">
                    {`${l.year ?? "—"} · ${l.price?.toLocaleString("ru") ?? "—"} ${l.currency ?? ""}${oldPrice} · ${l.mileage_km?.toLocaleString("ru") ?? "—"} км`}
                  </div>
                </div>
                <button
                  className="btn btn--danger"
                  aria-label="Убрать из избранного"
                  onClick={(e) => {
                    e.stopPropagation();
                    void unfavorite(l.id);
                  }}
                >
                  ✕
                </button>
              </div>
            );
          })}
        </div>
      </section>
    </div>
  );
}
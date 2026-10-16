/** Экран «Избранное» (этап 6, ТЗ п. 9.2.4: toggle + список). */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Cell, List, Placeholder, Section, Spinner } from "@telegram-apps/telegram-ui";

import { api } from "../api/client";
import { isGuest } from "../api/auth";
import { useLoginPrompt } from "../ui/LoginPrompt";
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
        <Button size="l" onClick={() => navigate("/login")}>
          Войти
        </Button>
      </div>
    );
  }
  if (error) {
    return (
      <Placeholder
        header="Нужна авторизация"
        description="Избранное доступно после входа через Telegram"
      />
    );
  }
  if (items === null) {
    return <Spinner size="l" />;
  }
  if (items.length === 0) {
    return (
      <Placeholder
        header="Пока пусто"
        description="Откройте объявление в ленте и нажмите «В избранное»"
      />
    );
  }

  return (
    <List>
      <Section header={`Избранное: ${items.length}`}>
        {items.map((l) => {
          // Бейджи отслеживания (PLAN.md, фаза 4): ⬇️ цена снизилась, 🔴 исчезло
          const dropped =
            l.first_price != null && l.price != null && l.price < l.first_price;
          const badge = l.is_active === false ? "🔴 " : dropped ? "⬇️ " : "";
          const oldPrice = dropped && l.first_price ? ` (было ${l.first_price.toLocaleString("ru")})` : "";
          return (
            <Cell
              key={l.id}
              subtitle={`${l.year ?? "—"} · ${l.price?.toLocaleString("ru") ?? "—"} ${l.currency ?? ""}${oldPrice} · ${l.mileage_km?.toLocaleString("ru") ?? "—"} км`}
              onClick={() => navigate(`/listing/${l.id}`)}
              after={
                <Button
                  mode="plain"
                  size="s"
                  onClick={(e: React.MouseEvent) => {
                    e.stopPropagation();
                    unfavorite(l.id);
                  }}
                >
                  ✕
                </Button>
              }
            >
              {`${badge}${l.make ?? ""} ${l.model ?? ""}`.trim() || "Объявление"}
            </Cell>
          );
        })}
      </Section>
    </List>
  );
}
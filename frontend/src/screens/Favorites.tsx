/** Экран «Избранное» (этап 6, ТЗ п. 9.2.4: toggle + список). */

import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Cell, List, Placeholder, Section, Spinner } from "@telegram-apps/telegram-ui";

import { api } from "../api/client";
import type { Listing } from "../api/types";

export function FavoritesScreen() {
  const [items, setItems] = useState<Listing[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const navigate = useNavigate();

  const load = useCallback(() => {
    api<Listing[]>("/favorites")
      .then(setItems)
      .catch((e: Error) => setError(e.message));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const unfavorite = async (id: number) => {
    await api(`/listings/${id}/favorite`, { method: "DELETE" });
    setItems((prev) => prev?.filter((x) => x.id !== id) ?? null);
  };

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
        {items.map((l) => (
          <Cell
            key={l.id}
            subtitle={`${l.year ?? "—"} · ${l.price?.toLocaleString("ru") ?? "—"} ${l.currency ?? ""} · ${l.mileage_km?.toLocaleString("ru") ?? "—"} км`}
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
            {`${l.make ?? ""} ${l.model ?? ""}`.trim() || "Объявление"}
          </Cell>
        ))}
      </Section>
    </List>
  );
}
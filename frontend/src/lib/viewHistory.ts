/**
 * История просмотров карточек (прод-фича 2026-10-25): каждое открытие
 * объявления пишется в localStorage — переживает перезапуск и работает
 * гостю. Ключ — id пользователя из JWT (sub) либо 'guest'.
 *
 * Запись — через CustomEvent "listing:view" (диспатчит ListingScreen):
 * экран истории подписан, сама история не импортирует экраны.
 */

export interface ViewedListing {
  listing_id: number;
  make?: string | null;
  model?: string | null;
  year?: number | null;
  price?: number | null;
  currency?: string | null;
  image?: string | null;
  viewed_at: string; // ISO
}

const CAP = 50;
const PREFIX = "autobot.views.";

/** Ключ пользователя: sub из access-токена (без библиотек), иначе 'guest'. */
export function userKey(): string {
  try {
    const token = localStorage.getItem("autobot.access");
    if (!token) return "guest";
    const payload = token.split(".")[1];
    if (!payload) return "guest";
    const json = JSON.parse(atob(payload.replace(/-/g, "+").replace(/_/g, "/")));
    return json.sub != null ? String(json.sub) : "guest";
  } catch {
    return "guest";
  }
}

function storageKey(): string {
  return PREFIX + userKey();
}

export function getViews(): ViewedListing[] {
  try {
    const raw = localStorage.getItem(storageKey());
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as ViewedListing[]) : [];
  } catch {
    return [];
  }
}

/** Добавить просмотр: дедуп по id (наверх), cap 50. */
export function addView(item: Omit<ViewedListing, "viewed_at"> & { viewed_at?: string }): void {
  const entry: ViewedListing = { ...item, viewed_at: item.viewed_at ?? new Date().toISOString() };
  const rest = getViews().filter((v) => v.listing_id !== entry.listing_id);
  const next = [entry, ...rest].slice(0, CAP);
  try {
    localStorage.setItem(storageKey(), JSON.stringify(next));
  } catch {
    // localStorage переполнен/недоступен — история не критична
  }
  window.dispatchEvent(new CustomEvent("listing:viewed"));
}

export function clearViews(): void {
  try {
    localStorage.removeItem(storageKey());
  } catch {
    /* noop */
  }
  window.dispatchEvent(new CustomEvent("listing:viewed"));
}

/** Подписка на обновления (экран «История»). Возвращает отписку. */
export function subscribeViews(cb: () => void): () => void {
  const handler = () => cb();
  window.addEventListener("listing:viewed", handler);
  // смена пользователя (вход/выход) — тоже обновить
  window.addEventListener("storage", handler);
  return () => {
    window.removeEventListener("listing:viewed", handler);
    window.removeEventListener("storage", handler);
  };
}

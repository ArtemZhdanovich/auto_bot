/**
 * Оболочка Mini App (NAV-V2 §2): 4 таба — Главная, Авто, Чаты, Профиль.
 *
 * Мобайл: фиксированный Tabbar снизу (telegram-ui) — иконка + короткая
 * подпись, активный таб акцентом. Десктоп ≥768px: sidebar слева.
 *
 * Бейдж непрочитанных — на табе «Чаты» (единый стор чатов, useChatUnread:
 * события личного канала user:{uid} + поллинг-фоллбэк; muted не считаются).
 * Гость — чатов нет (06 §1), бейджа нет. Сегменты внутри табов:
 * Главная = Лента|AI (Home), Авто = Избранное|История|Мои объявления
 * (Auto). «Сигналы» (/filters), калькулятор и сравнение — стек-экраны
 * без места в таб-баре (ссылки из Ленты и Профиля).
 */

import { useLocation, useNavigate } from "react-router-dom";
import { Tabbar } from "@telegram-apps/telegram-ui";
import { Car, Home, MessageCircle, User, type LucideIcon } from "lucide-react";

import { useChatUnread } from "../lib/chatStore";
import { isGuest } from "../api/auth";

interface NavItem {
  path: string;
  icon: LucideIcon;
  /** Подпись в десктоп-sidebar */
  text: string;
  /** Короткая подпись в мобильном таббаре */
  short: string;
}

/** IA (NAV-V2): 4 раздела; у каждого — иконка и подпись. */
export const NAV_ITEMS: readonly NavItem[] = [
  { path: "/", icon: Home, text: "Главная", short: "Главная" },
  { path: "/auto", icon: Car, text: "Авто", short: "Авто" },
  { path: "/chats", icon: MessageCircle, text: "Чаты", short: "Чаты" },
  { path: "/profile", icon: User, text: "Профиль", short: "Профиль" },
] as const;

/** Бейдж чатов: непрочитанные из единного стора; гостю — нет. */
function useChatTabBadge(): number {
  const unread = useChatUnread();
  return isGuest() ? 0 : unread;
}

/** Активность таба: точное совпадение; «Чаты» активны и на /chats/:id
 *  (оверлей комнаты поверх keep-alive списка). */
function useIsActive(): (path: string) => boolean {
  const { pathname } = useLocation();
  const p = pathname !== "/" && pathname.endsWith("/") ? pathname.slice(0, -1) : pathname;
  return (path: string) =>
    path === "/" ? p === "/" || p === "" : p === path || p.startsWith(`${path}/`);
}

/** Мобильный таб-бар (telegram-ui Tabbar) / десктопный sidebar (>= 768px). */
export function Shell({ desktop = false }: { desktop?: boolean }) {
  const navigate = useNavigate();
  const isActive = useIsActive();
  const unread = useChatTabBadge();
  const unreadLabel = unread > 99 ? "99+" : String(unread);

  if (desktop) {
    return (
      <nav className="app-sidebar" aria-label="Основная навигация">
        <div className="sidebar-brand">
          <span>🚗 Auto Selection</span>
        </div>
        {NAV_ITEMS.map((it) => {
          const active = isActive(it.path);
          return (
            <button
              key={it.path}
              type="button"
              className={active ? "sidebar-item sidebar-item--active" : "sidebar-item"}
              aria-current={active ? "page" : undefined}
              aria-label={
                it.path === "/chats" && unread > 0
                  ? `Чаты: ${unread} непрочитанных`
                  : undefined
              }
              onClick={() => navigate(it.path)}
            >
              <it.icon size={20} aria-hidden="true" />
              <span>{it.text}</span>
              {it.path === "/chats" && unread > 0 && (
                <span className="sidebar-item__badge" aria-hidden="true">
                  {unreadLabel}
                </span>
              )}
            </button>
          );
        })}
      </nav>
    );
  }

  return (
    <nav aria-label="Основная навигация">
      <Tabbar className="app-tabbar" style={{ position: "fixed", bottom: 0, left: 0, right: 0 }}>
        {NAV_ITEMS.map((it) => {
          const active = isActive(it.path);
          return (
            <Tabbar.Item
              key={it.path}
              text={it.short}
              selected={active}
              aria-current={active ? "page" : undefined}
              className={active ? "app-tabbar__item--active" : undefined}
              aria-label={
                it.path === "/chats" && unread > 0
                  ? `Чаты: ${unread} непрочитанных`
                  : undefined
              }
              onClick={() => navigate(it.path)}
            >
              <span className="app-tabbar__icon">
                <it.icon size={22} aria-hidden="true" />
                {it.path === "/chats" && unread > 0 && (
                  <span className="app-tabbar__badge" aria-hidden="true">
                    {unreadLabel}
                  </span>
                )}
              </span>
            </Tabbar.Item>
          );
        })}
      </Tabbar>
    </nav>
  );
}

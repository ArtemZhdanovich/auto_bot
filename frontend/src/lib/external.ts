/**
 * Открыть внешнюю ссылку из миниаппа: в Telegram только WebApp.openLink()
 * проходит через WebView-мост; target="_blank" и window.open там молча
 * не работают. В обычном браузере — обычный window.open.
 */
import { getWebApp } from "../api/auth";

export function openExternal(url: string): void {
  const wa = getWebApp();
  if (wa?.openLink) {
    wa.openLink(url, { try_instant_view: false });
  } else {
    window.open(url, "_blank", "noopener,noreferrer");
  }
}

/**
 * «Поделиться» лотом: уводит в выбор чата inline-режима бота с запросом
 * `listing:<id>` — бот отвечает карточкой с фото, кнопкой «Открыть в
 * приложении» (t.me/<bot>?startapp=listing_<id>) и ссылкой на источник.
 * Вне Telegram (или если inline выключен) — false, вызывающий показывает тост.
 */
export function shareListing(listingId: number): boolean {
  const wa = getWebApp();
  if (wa?.switchInlineQuery) {
    wa.switchInlineQuery(`listing:${listingId}`);
    return true;
  }
  return false;
}

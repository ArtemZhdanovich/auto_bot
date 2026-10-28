/**
 * Открытие Telegram Stars-инвойса (ТЗ п. 9.6): tg.openInvoice с колбеком
 * статуса; вне Telegram (браузер/десктоп-ТГ без mini app api) — фолбэк:
 * открываем ссылку в новой вкладке и возвращаем false, чтобы экран показал
 * подсказку «оплата ★ работает только в Telegram».
 *
 * Вынесено из Profile.tsx (06-market-user-listings F1: теперь инвойсы
 * открывают и размещения рынка — «Продлить» в «Моих объявлениях»).
 */

export type InvoiceStatus = "paid" | "failed" | "cancelled" | "pending";

/** Внутри Telegram ли открыт мини-апп (оплата Stars работает только там). */
export function inTelegram(): boolean {
  return Boolean(
    (window as unknown as { Telegram?: { WebApp?: { initDataUnsafe?: unknown } } })
      .Telegram?.WebApp?.initDataUnsafe,
  );
}

/**
 * Открыть инвойс. Возвращает true, если открыт нативный диалог Telegram
 * (статус придёт в onStatus); false — открыли ссылку в новой вкладке
 * (статуса не будет — зовём onStatus("pending") не нужно, экран сам
 * покажет подсказку про оплату только в Telegram).
 */
export function openInvoice(url: string, onStatus: (s: InvoiceStatus) => void): boolean {
  const webApp = (
    window as unknown as {
      Telegram?: { WebApp?: { openInvoice?: (u: string, cb: (s: string) => void) => void } };
    }
  ).Telegram?.WebApp;
  if (webApp?.openInvoice) {
    webApp.openInvoice(url, (status) => onStatus(status as InvoiceStatus));
    return true;
  }
  window.open(url, "_blank"); // вне Telegram — просто открываем ссылку
  return false;
}

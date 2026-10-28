/**
 * Загрузка фото лота (07-sell-chats-partner-inspection.md §1):
 * POST /api/market/photos — multipart form-data (поле file),
 * image/jpeg|png|webp ≤8МБ → {url}. Грузим по одному файлу.
 *
 * Не через api-клиент: он ставит Content-Type: application/json, что
 * ломает multipart-границу. JWT и авто-refresh (401 → refresh-запрос →
 * один ретрай) делаем вручную поверх fetch.
 */

import { api, getAccessToken } from "../api/client";

/** Лимит одного файла (контракт §1). */
export const MAX_PHOTO_MB = 8;

/** Разрешённые MIME-типы (контракт §1: jpeg|png|webp). */
export const PHOTO_MIME_RE = /^image\/(jpeg|png|webp)$/;

/** Максимальный размер файла в байтах (для проверки до отправки). */
export const MAX_PHOTO_BYTES = MAX_PHOTO_MB * 1024 * 1024;

/** Файл проходит клиентские проверки контракта (тип + размер). */
export function isAcceptablePhoto(file: File): boolean {
  return PHOTO_MIME_RE.test(file.type) && file.size <= MAX_PHOTO_BYTES;
}

/** Один POST /api/market/photos с FormData; 401 → refresh-триггер + ретрай. */
export async function uploadMarketPhoto(file: File): Promise<string> {
  const attempt = (): Promise<Response> => {
    const fd = new FormData();
    fd.append("file", file);
    const token = getAccessToken();
    return fetch("/api/market/photos", {
      method: "POST",
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      body: fd,
    });
  };

  let resp = await attempt();
  if (resp.status === 401) {
    // Провоцируем авто-refresh api-клиента любым защищённым GET (клиент
    // сам обновит access-токен в хранилище или уйдёт в гостевой фоллбэк),
    // затем один повтор загрузки с уже свежим токеном.
    await api("/me").catch(() => undefined);
    resp = await attempt();
  }

  const body = (await resp.json().catch(() => ({}))) as { url?: unknown; detail?: unknown };
  if (!resp.ok || typeof body.url !== "string") {
    const detail = typeof body.detail === "string" ? body.detail : `HTTP ${resp.status}`;
    throw new Error(`Загрузка фото не удалась: ${detail}`);
  }
  return body.url;
}

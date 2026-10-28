/**
 * HTTP-клиент Mini App: JWT из initData-авторизации,
 * базовый URL /api (в dev проксируется vite-ом на backend).
 *
 * Сессия персистентная: access/refresh живут в localStorage
 * (lib/persistentState) и переживают F5/перезапуск аппки. При 401 на
 * защищённом запросе — один авто-refresh (POST /api/auth/refresh,
 * single-flight от параллельных вызовов) и повтор исходного запроса.
 * Если refresh не прошёл — дергаем sessionExpired-обработчик из App
 * (тихий переход в гостевой режим, без экрана входа) и отдаём исходную
 * ошибку наверх.
 */

import { persistentGet, persistentRemove, persistentSet } from "../lib/persistentState";

const ACCESS_KEY = "autobot.access";
const REFRESH_KEY = "autobot.refresh";

export function getAccessToken(): string | null {
  return persistentGet(ACCESS_KEY);
}

export function getRefreshToken(): string | null {
  return persistentGet(REFRESH_KEY);
}

export function setTokens(access: string, refresh: string): void {
  persistentSet(ACCESS_KEY, access);
  persistentSet(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  persistentRemove(ACCESS_KEY);
  persistentRemove(REFRESH_KEY);
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

// ---------- Авто-refresh сессии (POST /api/auth/refresh, {refresh_token}) ----------

type SessionExpiredHandler = () => void | Promise<void>;

let sessionExpiredHandler: SessionExpiredHandler | null = null;

/** Обработчик смерти сессии: App регистрирует тихий переход в гостевой
 *  режим (в Telegram — повторный authorize). */
export function setSessionExpiredHandler(handler: SessionExpiredHandler | null): void {
  sessionExpiredHandler = handler;
}

/** Промис-синглтон: параллельные 401 ждут один и тот же refresh-запрос. */
let refreshPromise: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const refresh = getRefreshToken();
  if (!refresh) return false;
  try {
    const resp = await fetch("/api/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: refresh }),
    });
    if (!resp.ok) return false;
    const pair = (await resp.json()) as { access_token?: string; refresh_token?: string };
    if (!pair.access_token || !pair.refresh_token) return false;
    setTokens(pair.access_token, pair.refresh_token);
    return true;
  } catch {
    return false; // сеть/битый JSON — считаем refresh непрошедшим
  }
}

function refreshOnce(): Promise<boolean> {
  if (!refreshPromise) {
    refreshPromise = refreshTokens().finally(() => {
      refreshPromise = null;
    });
  }
  return refreshPromise;
}

/** Тихий гостевой fallback — тоже single-flight: пачка параллельных
 *  неудачных refresh не должна устроить гонку guestLogin-ов. */
let fallbackPromise: Promise<void> | null = null;

function fallBackToGuest(): Promise<void> {
  if (!fallbackPromise) {
    fallbackPromise = Promise.resolve(
      sessionExpiredHandler ? sessionExpiredHandler() : undefined,
    )
      .catch(() => undefined) // восстановиться не удалось — ошибку запроса отдаём наверх
      .finally(() => {
        fallbackPromise = null;
      });
  }
  return fallbackPromise;
}

/**
 * Выполнить запрос. canRecover=false у повтора после refresh — защита
 * от бесконечного цикла 401 → refresh → 401. Auth-роуты (/auth/*)
 * сами управляют токенами, их 401 = неверные учётные данные, без
 * авто-восстановления (иначе guestLogin рекурсивно ловил бы свой же 401).
 */
async function request<T>(path: string, options: RequestInit, canRecover: boolean): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((options.headers as Record<string, string>) ?? {}),
  };
  const token = getAccessToken();
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }

  const resp = await fetch(`/api${path}`, { ...options, headers });

  if (resp.status === 204) {
    return undefined as T;
  }

  if (resp.status === 401 && canRecover && !path.startsWith("/auth/")) {
    if (await refreshOnce()) {
      return request<T>(path, options, false); // один повтор с новым access
    }
    // refresh не прошёл — тихо уходим в гостевой режим (токены не стираем:
    // «Выход» в профиле — единственный способ разлогиниться), исходную
    // ошибку бросаем: пользователь остаётся на текущем экране
    await fallBackToGuest();
  }

  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail =
      typeof body?.detail === "string" ? body.detail : resp.statusText;
    throw new ApiError(resp.status, detail);
  }
  return body as T;
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
  return request<T>(path, options, true);
}

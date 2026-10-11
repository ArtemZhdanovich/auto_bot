/**
 * HTTP-клиент Mini App: JWT из initData-авторизации,
 * базовый URL /api (в dev проксируется vite-ом на backend).
 */

const ACCESS_KEY = "autobot.access";
const REFRESH_KEY = "autobot.refresh";

export function getAccessToken(): string | null {
  return sessionStorage.getItem(ACCESS_KEY);
}

export function setTokens(access: string, refresh: string): void {
  sessionStorage.setItem(ACCESS_KEY, access);
  sessionStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  sessionStorage.removeItem(ACCESS_KEY);
  sessionStorage.removeItem(REFRESH_KEY);
}

export class ApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
): Promise<T> {
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
  const body = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    const detail =
      typeof body?.detail === "string" ? body.detail : resp.statusText;
    throw new ApiError(resp.status, detail);
  }
  return body as T;
}

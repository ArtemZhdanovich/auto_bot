/** Авторизация Mini App: initData → POST /api/auth/telegram → JWT (ТЗ п. 9.1). */

import { api, clearTokens, setTokens } from "./client";
import type { AuthResponse } from "./types";

interface WebAppUser {
  id: number;
  first_name?: string;
  last_name?: string;
  username?: string;
  language_code?: string;
}

interface TelegramWebApp {
  initData: string;
  initDataUnsafe?: { user?: WebAppUser };
  ready: () => void;
  expand: () => void;
  themeParams?: Record<string, string>;
  HapticFeedback?: { impactOccurred: (s: string) => void };
}

declare global {
  interface Window {
    Telegram?: { WebApp?: TelegramWebApp };
  }
}

export function getWebApp(): TelegramWebApp | null {
  return window.Telegram?.WebApp ?? null;
}

/** Авторизован ли контекст (открыто внутри Telegram). */
export function isTelegramContext(): boolean {
  return Boolean(getWebApp()?.initData);
}

/** Обмен initData на JWT. Вне Telegram возвращает null (read-only режим). */
export async function authorize(): Promise<AuthResponse | null> {
  const wa = getWebApp();
  if (!wa?.initData) {
    return null;
  }
  wa.ready();
  wa.expand();
  const resp = await api<AuthResponse>("/auth/telegram", {
    method: "POST",
    body: JSON.stringify({ init_data: wa.initData }),
  });
  setTokens(resp.access_token, resp.refresh_token);
  return resp;
}

export function logout(): void {
  clearTokens();
}

export function tgUser(): WebAppUser | null {
  return getWebApp()?.initDataUnsafe?.user ?? null;
}

export function haptic(kind = "light"): void {
  getWebApp()?.HapticFeedback?.impactOccurred(kind);
}

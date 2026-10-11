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

/** Обмен initData на JWT. Вне Telegram возвращает null (веб-режим: логин формой). */
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

/** Регистрация на сайте: email + пароль → JWT (поиск доступен после регистрации). */
export async function webRegister(email: string, password: string, firstName?: string): Promise<AuthResponse> {
  const resp = await api<AuthResponse>("/auth/register", {
    method: "POST",
    body: JSON.stringify({ email, password, first_name: firstName || null }),
  });
  setTokens(resp.access_token, resp.refresh_token);
  return resp;
}

/** Вход на сайте: email + пароль → JWT. */
export async function webLogin(email: string, password: string): Promise<AuthResponse> {
  const resp = await api<AuthResponse>("/auth/login", {
    method: "POST",
    body: JSON.stringify({ email, password }),
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

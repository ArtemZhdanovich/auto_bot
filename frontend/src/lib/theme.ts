/**
 * Тема миниаппа (светлая/тёмная): выбор пользователя хранится в localStorage
 * ("autobot.theme", default "system"), применённая тема — в data-theme на <html>
 * (палитра — блок :root[data-theme="dark"] в index.css).
 *
 * system: тема Telegram (colorScheme), вне Telegram — тема ОС
 * (prefers-color-scheme). light/dark — явный выбор, систему игнорирует.
 */

export type Theme = "system" | "light" | "dark";
export type Appearance = "light" | "dark";

const THEME_KEY = "autobot.theme";

/** Сохранённый выбор пользователя (default "system"). */
export function getTheme(): Theme {
  try {
    const saved = localStorage.getItem(THEME_KEY);
    if (saved === "system" || saved === "light" || saved === "dark") return saved;
  } catch {
    /* private mode */
  }
  return "system";
}

/** Тема Telegram → appearance (раньше tgAppearance в App.tsx); вне Telegram — ОС. */
export function tgAppearance(): Appearance {
  const cs = window.Telegram?.WebApp?.colorScheme;
  if (cs === "dark" || cs === "light") return cs;
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** Итоговый вид по режиму: system — Telegram colorScheme / ОС, иначе явный. */
export function resolveAppearance(theme: Theme): Appearance {
  return theme === "system" ? tgAppearance() : theme;
}

/** Применить тему: data-theme на <html> + appearance для AppRoot telegram-ui. */
export function applyTheme(theme: Theme = getTheme()): Appearance {
  const appearance = resolveAppearance(theme);
  document.documentElement.dataset.theme = appearance;
  return appearance;
}

type ThemeListener = (appearance: Appearance) => void;

const listeners = new Set<ThemeListener>();

/** Сохранить выбор, применить сразу и оповестить подписчиков (App/Profile). */
export function setTheme(theme: Theme): Appearance {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    /* private mode */
  }
  const appearance = applyTheme(theme);
  listeners.forEach((fn) => fn(appearance));
  return appearance;
}

/** Подписка на смену темы (setTheme) — MiniApp обновляет AppRoot. */
export function subscribeTheme(fn: ThemeListener): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Подписка на смену системной темы — активна только в режиме system. */
export function watchSystemTheme(fn: ThemeListener): () => void {
  const mq = window.matchMedia?.("(prefers-color-scheme: dark)");
  if (!mq || typeof mq.addEventListener !== "function") return () => undefined;
  const onChange = () => {
    if (getTheme() === "system") fn(applyTheme());
  };
  mq.addEventListener("change", onChange);
  return () => mq.removeEventListener("change", onChange);
}

/**
 * Персистентное key-value хранилище поверх localStorage.
 *
 * Почему localStorage, а не sessionStorage: сессия должна переживать F5
 * и перезапуск Mini App — токены (api/client) и флаг гостя (api/auth)
 * хранятся здесь; «Выход» в профиле — единственный способ их стереть.
 *
 * Если localStorage недоступен (приватный режим Safari, SSR) — тихий
 * fallback на Map в памяти: приложение продолжает работать, но уже
 * без персистентности между перезагрузками.
 */

const memory = new Map<string, string>();

/** Один раз при загрузке модуля: доступен ли localStorage для записи. */
function resolveStorage(): Storage | null {
  try {
    const ls = globalThis.localStorage;
    if (!ls) return null;
    const probe = "__autobot.probe__";
    ls.setItem(probe, "1"); // приватный режим кидает прямо на setItem
    ls.removeItem(probe);
    return ls;
  } catch {
    return null;
  }
}

const storage = resolveStorage();

export function persistentGet(key: string): string | null {
  try {
    return storage ? storage.getItem(key) : (memory.get(key) ?? null);
  } catch {
    return memory.get(key) ?? null;
  }
}

export function persistentSet(key: string, value: string): void {
  // пишем и в зеркало в памяти: если storage посреди сессии отвалится,
  // значения не потеряются
  memory.set(key, value);
  try {
    storage?.setItem(key, value);
  } catch {
    // квота/недоступно — живём в памяти
  }
}

export function persistentRemove(key: string): void {
  memory.delete(key);
  try {
    storage?.removeItem(key);
  } catch {
    // аналогично
  }
}

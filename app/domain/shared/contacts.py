"""Общие паттерны контактов: телефоны и внешние мессенджеры (08 §3, 10 §2).

Доменный модуль без зависимостей: используется чатом (маскировка
сообщений, `app.application.chat.privacy`) и модерацией лотов рынка
(детект контактов в описании, `app.application.market.moderation`).

Что распознаётся (08 §9 — риск зацепить VIN/госномер исключён паттерном):
- телефоны: явный префикс `+` или `8` и >=9 цифр всего, между цифрами
  допускаются пробелы/дефисы/скобки (`+7 999 123-45-67`, `88005553535`,
  `+82-10-1234-5678`, `+375 (29) 123-45-67`);
- мессенджеры: `wa.me/...`, `api.whatsapp.com/...`, `web.whatsapp.com`,
  `t.me/<username>` — кроме `t.me/share` (шаринг нашей карточки) и
  `t.me/<BOT_USERNAME>` (наш бот; пустой конфиг = распознаём все t.me,
  это безопаснее, 08 §9).

НЕ распознаются: VIN (буквы не проходят телефонный паттерн), 8-значные
коды (меньше 9 цифр), encar.com / kcar.com / наши домены и localhost.
"""

from __future__ import annotations

import re

# Плейсхолдеры маскировки (08 §3): объясняют, почему контакт скрыт.
PHONE_MASK = "📱 [номер скрыт — обмен контактами только через сделку]"
MESSENGER_MASK = "🔗 [внешний мессенджер скрыт — продолжайте сделку здесь]"

# Телефон: '+' или '8' в начале + ещё >=8 цифр (итого >=9, E.164 <=15),
# внутри — пробелы/дефисы/скобки (возможны несколько подряд: «) 1»).
# Lookaround отсекает хвосты длинных числовых последовательностей
# (VIN/коды без префикса не подходят).
PHONE_RE = re.compile(r"(?<!\d)(?:\+|8)(?:[\s\-()]*\d){8,14}(?!\d)")

# WhatsApp-ссылки: any wa.me / api.whatsapp.com / web.whatsapp.com.
# Lookbehind не даёт зацепить t.me внутри чужих доменов («xwa.me»).
WHATSAPP_RE = re.compile(
    r"(?<![\w./])(?:https?://)?(?:www\.)?"
    r"(?:wa\.me|api\.whatsapp\.com|web\.whatsapp\.com)(?:/(?P<path>\S*))?",
    re.IGNORECASE,
)

# t.me/<username>: точный паттерн username (латиница/цифры/_), username
# сравнивается с bot_username и 'share' в колбэке замены.
TME_RE = re.compile(
    r"(?<![\w./])(?:https?://)?(?:www\.)?t\.me/(?P<user>[A-Za-z0-9_]{3,32})"
    r"(?P<rest>[/?#]\S*)?",
    re.IGNORECASE,
)


def mask_contacts(body: str, bot_username: str) -> str:
    """Заменить телефоны и ссылки внешних мессенджеров плейсхолдерами.

    Args:
        body: Тело сообщения (markdown, как введён пользователем).
        bot_username: Юзернейм нашего бота без `@` (t.me/<он> не
            маскируем); пустая строка — маскируются все t.me-ссылки.

    Returns:
        Текст с плейсхолдерами вместо контактов.
    """
    body = PHONE_RE.sub(PHONE_MASK, body)
    body = WHATSAPP_RE.sub(MESSENGER_MASK, body)

    own = (bot_username or "").strip().lstrip("@").lower()

    def _tme_replace(match: re.Match[str]) -> str:
        # t.me/share/... — шаринг нашей карточки, не маскируем (08 §3);
        # t.me/<наш бот> — тоже (ссылки на вход через бота).
        username = match.group("user").lower()
        if username == "share" or (own and username == own):
            return match.group(0)
        return MESSENGER_MASK

    return TME_RE.sub(_tme_replace, body)


def find_contacts(body: str, bot_username: str = "") -> list[str]:
    """Найти контакты в тексте (10 §2: правило contacts_in_text).

    Те же паттерны, что и маскировка (без дублирования логики): телефон,
    whatsapp-ссылка, t.me/<username> — кроме t.me/share и t.me/<наш бот>.

    Args:
        body: Проверяемый текст (описание лота).
        bot_username: Юзернейм нашего бота без `@` (его t.me — не контакт).

    Returns:
        Найденные фрагменты в порядке появления; пустой список — чисто.
    """
    found: list[str] = [m.group(0) for m in PHONE_RE.finditer(body)]
    found += [m.group(0) for m in WHATSAPP_RE.finditer(body)]
    own = (bot_username or "").strip().lstrip("@").lower()
    for match in TME_RE.finditer(body):
        username = match.group("user").lower()
        if username == "share" or (own and username == own):
            continue
        found.append(match.group(0))
    return found

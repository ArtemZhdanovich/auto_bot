"""Хендлеры бота. Команды + AI-диалог через backend (этап 3, ТЗ п. 7)."""

import html
import logging
import re

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LabeledPrice,
    Message,
    PreCheckoutQuery,
)

from app.bot.client import BackendClient

logger = logging.getLogger(__name__)
router = Router(name="main")

backend = BackendClient()

HELP_TEXT = (
    "<b>Команды:</b>\n"
    "/start — онбординг\n"
    "/help — справка\n"
    "/reset — сбросить текущий диалог\n"
    "/calc — расчёт доставки и растаможки (цена, объём, год)\n"
    "/stop — отключить уведомления\n"
    "/resume — включить уведомления\n"
    "/subscribe — подписка Pro/Business (Stars)\n\n"
    "Просто напишите текстом, какую машину ищете — я помогу с подбором."
)

START_TEXT = (
    "👋 Привет, {name}! Я Артём — ваш менеджер по авто из Кореи. 🇰🇷\n\n"
    "Подбираю машины с encar и kcar, считаю цену «под ключ» с доставкой "
    "и растаможкой, слежу за новыми лотами.\n\n"
    "Опишите словами, что ищете — например: "
    "<i>«Hyundai Palisade 2022–2024, дизель, до 45к $»</i>.\n\n"
    "Или откройте приложение — там лента, карточки и калькулятор доставки."
)


COUNTRY_LABELS = {
    "KR": "🇰🇷 Корея",
    "CN": "🇨🇳 Китай",
    "DE": "🇩🇪 Германия",
    "US": "🇺🇸 США",
    "JP": "🇯🇵 Япония",
}

_FIELD_LABELS = [
    ("make", "Марка"),
    ("model", "Модель"),
    ("year_from", "Год от"),
    ("year_to", "Год до"),
    ("price_min", "Цена от"),
    ("price_max", "Цена до"),
    ("currency", "Валюта"),
    ("mileage_max", "Пробег до, км"),
    ("fuel", "Топливо"),
    ("transmission", "КПП"),
    ("drive", "Привод"),
    ("body_type", "Кузов"),
    ("color", "Цвет"),
    ("engine_volume_min", "Объём от"),
    ("engine_volume_max", "Объём до"),
    ("country", "Страна"),
    ("location", "Локация"),
]


def _fmt_value(field: str, value: object) -> str:
    """Значение критерия по-человечески (страны — флагами, цены — с пробелами)."""
    if field == "country":
        return COUNTRY_LABELS.get(str(value), str(value))
    if field in ("price_min", "price_max", "mileage_max"):
        try:
            return f"{int(str(value)):,}".replace(",", " ")
        except (TypeError, ValueError):
            return str(value)
    if field == "sources" and isinstance(value, list):
        return ", ".join(str(s) for s in value)
    return str(value)


def format_criteria(criteria: dict) -> str:
    """Критерии поиска человекочитаемым списком (никакого JSON)."""
    lines = []
    for field, label in _FIELD_LABELS:
        value = criteria.get(field)
        if value is not None and value != "":
            lines.append(f"• {label}: <b>{html.escape(_fmt_value(field, value))}</b>")
    if criteria.get("sources"):
        lines.append(
            "• Источники: <b>"
            + html.escape(", ".join(str(s) for s in criteria["sources"]))
            + "</b>"
        )
    return "\n".join(lines)


def _search_ready(criteria: dict) -> bool:
    """Эвристика готовности (те же поля, что в промпте агента)."""
    has_car = bool(criteria.get("make") or criteria.get("model"))
    has_price = bool(criteria.get("price_max") or criteria.get("price_min"))
    has_geo = bool(criteria.get("country") or criteria.get("sources"))
    return has_car and has_price and has_geo


def _miniapp_button(text: str = "📱 Мини-приложение", path: str = "") -> InlineKeyboardButton | None:
    """Кнопка в мини-апп: web_app только для https (требование Telegram),
    для http — обычная url-кнопка (откроется в браузере).
    path — маршрут внутри приложения (напр. «/calc?price=18000»)."""
    from aiogram.types import WebAppInfo

    url = _miniapp_url()
    if not url:
        return None
    if path:
        url = url.rstrip("/") + (path if path.startswith("/") else "/" + path)
    if url.startswith("https://"):
        return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))
    return InlineKeyboardButton(text=text, url=url)


def build_dialog_kb(reply: dict, turn: int, criteria: dict) -> InlineKeyboardMarkup:
    """Инлайн-панель активного ассистента: опции + навигация (ТЗ п. 7.4).

    Кнопки опций несут токен хода (turn) — клики по устаревшим кнопкам
    игнорируются и не ломают ход диалога при множественном выборе.
    """
    rows = [
        [InlineKeyboardButton(text=opt, callback_data=f"dlg:opt:{turn}:{i}")]
        for i, opt in enumerate(reply.get("options", []))
    ]
    if reply.get("finalize_ready") or _search_ready(criteria):
        rows.append(
            [InlineKeyboardButton(text="🔎 Запустить поиск", callback_data="dlg:finalize")]
        )
    miniapp = _miniapp_button()
    if miniapp:
        rows.append([miniapp])
    rows.append([InlineKeyboardButton(text="🔄 Сбросить диалог", callback_data="dlg:reset")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _panel_text(reply: dict, criteria: dict) -> str:
    """Текст панели ассистента: ответ + заполненные критерии списком."""
    text = html.escape(reply.get("text", "")).strip()
    filled = {k: v for k, v in (criteria or {}).items() if v is not None and v != []}
    if filled:
        text += "\n\n📋 <b>Параметры поиска:</b>\n" + format_criteria(filled)
    return text


async def _show_panel(
    message: Message, state: FSMContext, text: str, kb: InlineKeyboardMarkup
) -> None:
    """Одно «живое» сообщение-панель: редактируется на месте вместо новых."""
    data = await state.get_data()
    panel_id = data.get("panel_msg_id")
    if panel_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=panel_id,
                text=text,
                reply_markup=kb,
            )
            return
        except Exception:
            logger.debug("panel_edit_missed: msg_id=%s", panel_id)
    panel = await message.answer(text, reply_markup=kb)
    await state.update_data(panel_msg_id=panel.message_id)


async def send_dialog_reply(message: Message, reply: dict, state: FSMContext) -> None:
    """Показать ответ агента на панели; опции храним в FSM с токеном хода."""
    data = await state.get_data()
    turn = int(data.get("dlg_turn", 0)) + 1
    options = reply.get("options", [])[:8]
    criteria = reply.get("criteria") or {}
    await state.update_data(dlg_turn=turn, dlg_options=options, dlg_criteria=criteria)
    await _show_panel(message, state, _panel_text(reply, criteria), build_dialog_kb(reply, turn, criteria))


@router.message(CommandStart(deep_link=True))
@router.message(CommandStart())
async def cmd_start(message: Message, command: CommandObject | None, state: FSMContext) -> None:
    """Онбординг: сохраняем пользователя и сбрасываем состояние."""
    await state.clear()

    args = command.args if command else None

    user = message.from_user
    if user:
        await backend.upsert_user(
            telegram_id=user.id,
            username=user.username,
            first_name=user.first_name,
            last_name=user.last_name,
            language_code=user.language_code,
        )

    if args and await handle_deep_link(message, args):
        return

    name = user.first_name if user and user.first_name else "друг"
    await message.answer(
        START_TEXT.format(name=name), reply_markup=build_main_menu_kb()
    )


def _miniapp_url() -> str:
    """«Сырой» URL Mini App из настроек (без фильтрации по схеме).

    Фильтрация https — в _miniapp_button: web_app-кнопка Telegram требует
    https, а для http делаем обычную url-кнопку (откроется в браузере).
    """
    from app.core.config import get_settings

    return (get_settings().miniapp_url or "").strip()


def build_listing_caption(listing: dict) -> str:
    """Короткая карточка объявления для бота (UI/UX ТЗ §5.5)."""
    title = " ".join(
        x for x in [listing.get("make"), listing.get("model")] if x
    ) or "Автомобиль"
    price = listing.get("price")
    price_str = (
        f"{price:,.0f}".replace(",", " ") + f" {listing.get('currency') or ''}".rstrip()
        if price
        else "цена не указана"
    )
    details = []
    if listing.get("year"):
        details.append(str(listing["year"]))
    if listing.get("mileage_km"):
        details.append(f"{listing['mileage_km']:,.0f} км".replace(",", " "))
    if listing.get("vin"):
        details.append(f"VIN {listing['vin']}")
    caption = f"<b>{html.escape(title)}</b>\n💰 {html.escape(price_str)}"
    if details:
        caption += "\n⚙️ " + html.escape(" · ".join(details))
    if listing.get("source"):
        caption += f"\n📍 Источник: {html.escape(listing['source'])}"
    return caption[:1024]


def build_listing_kb(url: str) -> InlineKeyboardMarkup:
    """Кнопки карточки: приложение (фото/детали) + источник (UI/UX ТЗ §5.5)."""
    rows: list[list[InlineKeyboardButton]] = []
    miniapp = _miniapp_button("📱 Фото и детали")
    if miniapp:
        rows.append([miniapp])
    if url:
        rows.append([InlineKeyboardButton(text="🔗 Открыть у источника", url=url)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def handle_deep_link(message: Message, args: str) -> bool:
    """Deep links (UI/UX ТЗ §5.3): listing_{id}, filter_{id}, ref_, app.

    True — deep link обработан и обычный онбординг не нужен.
    """
    if args.startswith("listing_"):
        try:
            listing_id = int(args.removeprefix("listing_"))
        except ValueError:
            return False
        listing = await backend.get_listing(listing_id)
        if listing is None:
            await message.answer(
                "Объявление не найдено или сервис временно недоступен."
            )
            return True
        caption = build_listing_caption(listing)
        kb = build_listing_kb(listing.get("url") or "")
        images = listing.get("images") or []
        if images:
            await message.answer_photo(images[0], caption=caption, reply_markup=kb)
        else:
            await message.answer(caption, reply_markup=kb)
        return True

    if args.startswith("filter_"):
        miniapp = _miniapp_button("📱 Открыть фильтр в приложении")
        kb = InlineKeyboardMarkup(inline_keyboard=[[miniapp]]) if miniapp else None
        await message.answer(
            "🔍 Фильтр найден — управление им в приложении.", reply_markup=kb
        )
        return True

    if args.startswith("ref_"):
        # Реферальная программа — P4 (бэкенд), пока фиксируем переход в лог.
        logger.info(
            "referral_start: referrer=%s user=%s",
            args.removeprefix("ref_"),
            message.from_user.id if message.from_user else None,
        )
        return False

    return False


def _price_usd(listing: dict) -> float | None:
    """Цена объявления в USD (KRW/EUR конвертируем по фиксированному курсу MVP)."""
    price = listing.get("price")
    if not price:
        return None
    currency = (listing.get("currency") or "USD").upper()
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if currency == "KRW":
        return round(p / 1350.0)
    if currency == "EUR":
        return round(p * 1.08)
    return round(p)


def _format_calc_out(out: dict) -> str:
    """Человекочитаемая смета из ответа /api/calc/delivery."""
    header = (
        f"Авто {out['price_usd']:,.0f} $ · {out['engine_cc']} см³ · "
        f"{out['year']} г. ({out['age_group']})\n"
    ).replace(",", " ")
    lines = [
        "🚚 <b>Расчёт «под ключ»</b> (Корея → Владивосток)\n",
        header,
    ]
    for c in out.get("components", []):
        lines.append(f"• {c['label']}: {c['rub']:,.0f} ₽".replace(",", " "))
    lines.append(
        f"\n<b>Итого: ~{out['total_rub']:,.0f} ₽ (~{out['total_usd']:,.0f} $)</b>".replace(",", " ")
    )
    lines.append("\n<i>Оценка для физлица (личное пользование). Точную смету даст менеджер.</i>")
    return "\n".join(lines)


def _car_card_kb(listing: dict) -> InlineKeyboardMarkup:
    """Кнопки фото-карточки: объявление, доставка, приложение."""
    rows: list[list[InlineKeyboardButton]] = []
    if listing.get("url"):
        rows.append([InlineKeyboardButton(text="🔗 Открыть объявление", url=listing["url"])])
    rows.append(
        [
            InlineKeyboardButton(
                text="🚚 Доставка и растаможка", callback_data=f"calc:{listing['id']}"
            )
        ]
    )
    mini = _miniapp_button("📱 Смотреть в приложении")
    if mini:
        rows.append([mini])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_car_cards(message: Message, items: list[dict]) -> None:
    """Фото-карточки топ-объявлений: картинка-хотлинк + объявление + доставка."""
    sent = 0
    for listing in items[:5]:
        images = listing.get("images") or []
        caption = build_listing_caption(listing)
        kb = _car_card_kb(listing)
        if images:
            try:
                await message.answer_photo(
                    photo=str(images[0]), caption=caption, reply_markup=kb
                )
                sent += 1
                continue
            except Exception as exc:  # картинка недоступна → текстовая карточка
                logger.debug("photo_send_failed id=%s: %s", listing.get("id"), exc)
        await message.answer(caption, reply_markup=kb)
        sent += 1
    if not sent:
        await message.answer(
            "Пока ничего не нашлось — поищу на источниках и пришлю уведомления."
        )


def build_main_menu_kb() -> InlineKeyboardMarkup:
    """Главное инлайн-меню бота (WebApp + быстрые действия)."""
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🚗 AI-подбор авто", callback_data="menu:ai"
            )
        ],
    ]
    miniapp_btn = _miniapp_button("📱 Открыть приложение")
    if miniapp_btn:
        rows.append([miniapp_btn])
    rows.append(
        [InlineKeyboardButton(text="🧮 Калькулятор доставки", callback_data="menu:calc")]
    )
    rows.extend([
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="menu:notify"),
            InlineKeyboardButton(text="⭐ Подписка", callback_data="menu:subscribe"),
        ],
        [
            InlineKeyboardButton(text="🔄 Сбросить диалог", callback_data="menu:reset"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help"),
        ],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


MENU_AI_HINT = (
    "🚗 <b>AI-подбор</b>\n\n"
    "Просто напишите сообщением, какую машину ищете — например:\n"
    "<i>«Hyundai Palisade 2022–2024, дизель, до 45к $»</i>\n\n"
    "Я задам уточняющие вопросы и запущу поиск по источникам."
)


@router.callback_query(F.data.startswith("menu:"))
async def main_menu_callback(cb, state: FSMContext) -> None:
    """Главное меню: AI-подбор, приложение, уведомления, подписка, помощь."""
    await cb.answer()
    action = (cb.data or "").split(":", 1)[1]
    user_id = cb.from_user.id

    if action == "ai":
        await state.clear()
        await cb.message.answer(MENU_AI_HINT)
        return
    if action == "notify":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="⚡ Instant", callback_data="menu:notify_set:instant")],
                [InlineKeyboardButton(text="📬 Дайджест (09:00/20:00)", callback_data="menu:notify_set:digest")],
                [InlineKeyboardButton(text="🔕 Выключить", callback_data="menu:notify_set:off")],
                [InlineKeyboardButton(text="🌙 Тихие часы", callback_data="menu:quiet")],
            ]
        )
        await cb.message.answer(
            "🔔 <b>Режим уведомлений</b> (применится ко всем вашим поискам):",
            reply_markup=kb,
        )
        return
    if action.startswith("notify_set:"):
        mode = action.split(":", 1)[1]
        result = await backend.notification_settings(user_id, mode)
        labels = {
            "instant": "⚡ Instant-уведомления включены.",
            "digest": "📬 Дайджест 09:00 и 20:00 включён.",
            "off": "🔕 Уведомления выключены.",
        }
        if result is None:
            await cb.message.answer("Backend недоступен, попробуйте позже.")
        else:
            await cb.message.answer(labels[mode])
        return
    if action == "quiet":
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="🌙 23:00–08:00", callback_data="menu:quiet_set:23:00-08:00")],
                [InlineKeyboardButton(text="🌙 22:00–10:00", callback_data="menu:quiet_set:22:00-10:00")],
                [InlineKeyboardButton(text="☀️ Без тихих часов", callback_data="menu:quiet_set:off")],
            ]
        )
        await cb.message.answer(
            "🌙 <b>Тихие часы</b> — ночью уведомления не приходят "
            "(дождутся утра). Выберите интервал:",
            reply_markup=kb,
        )
        return
    if action.startswith("quiet_set:"):
        value = action.split(":", 1)[1]
        if value == "off":
            result = await backend.quiet_hours(user_id, clear=True)
            text = "☀️ Тихие часы отключены."
        else:
            result = await backend.quiet_hours(user_id, value)
            start, end = value.split("-")
            text = f"🌙 Тихие часы {start}–{end} UTC установлены."
        if result is None:
            await cb.message.answer("Backend недоступен, попробуйте позже.")
        else:
            await cb.message.answer(text)
        return
    if action == "subscribe":
        # переиспользуем логику /subscribe
        fake = cb.message
        if fake is not None:
            await cmd_subscribe(fake)
        return
    if action == "reset":
        await state.clear()
        await backend.dialog_reset(user_id)
        await cb.message.answer(
            "Диалог сброшен. Опишите, какую машину ищете.",
            reply_markup=build_main_menu_kb(),
        )
        return
    if action == "calc":
        await cb.message.answer(
            "🧮 <b>Калькулятор доставки</b> (Корея → Владивосток)\n\n"
            "Пришлите одной строкой: <b>цена $, объём см³, год</b>\n"
            "Например: <code>18000 2000 2022</code>\n\n"
            "Или откройте калькулятор в приложении — там красивые карточки.",
            reply_markup=_miniapp_kb(),
        )
        return
    if action == "help":
        await cb.message.answer(HELP_TEXT, reply_markup=build_main_menu_kb())


@router.message(Command("menu"))
async def cmd_menu(message: Message) -> None:
    """Показать главное меню."""
    await message.answer("Меню:", reply_markup=build_main_menu_kb())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=build_main_menu_kb())


@router.message(Command("reset"))
async def cmd_reset(message: Message, state: FSMContext) -> None:
    """Сброс диалога: архив сессии + очистка Redis (ТЗ п. 7.6)."""
    await state.clear()
    user_id = message.from_user.id if message.from_user else 0
    await backend.dialog_reset(user_id)
    await message.answer("Диалог сброшен. Начните заново — опишите, какую машину ищете.")


@router.message(Command("stop"))
async def cmd_stop(message: Message) -> None:
    """Отключить все уведомления (ТЗ п. 8.2)."""
    user_id = message.from_user.id if message.from_user else 0
    result = await backend.notification_settings(user_id, "off")
    if result is None:
        await message.answer("Backend недоступен, попробуйте позже.")
    else:
        await message.answer("🔕 Уведомления отключены. /resume — включить обратно.")


@router.message(Command("resume"))
async def cmd_resume(message: Message) -> None:
    """Включить уведомления обратно — в режиме digest (ТЗ п. 8.2)."""
    user_id = message.from_user.id if message.from_user else 0
    result = await backend.notification_settings(user_id, "digest")
    if result is None:
        await message.answer("Backend недоступен, попробуйте позже.")
    elif result.get("updated", 0) == 0:
        await message.answer(
            "У вас пока нет сохранённых поисков. Опишите, какую машину ищете."
        )
    else:
        await message.answer("🔔 Уведомления включены (дайджест 09:00 и 20:00).")


# ---------- Подписка и Telegram Stars (этап 6, ТЗ п. 8.7) ----------


def _tier_label(tier: str, period_days: int, amount: int) -> str:
    period = "год" if period_days >= 365 else "мес"
    name = "Pro" if tier == "pro" else "Business"
    return f"⭐ {name} — {amount}★/{period}"


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message) -> None:
    """Подписка: статус тарифа + выбор варианта оплаты Stars (ТЗ п. 8.7)."""
    user_id = message.from_user.id if message.from_user else 0
    status = await backend.subscription_status(user_id)
    if status is None:
        await message.answer("Backend недоступен, попробуйте позже.")
        return

    tier = status.get("tier", "free")
    until = status.get("subscription_until")
    current = f"Текущий тариф: <b>{tier}</b>"
    if until:
        current += f" (до {str(until)[:10]})"
    text = (
        f"{current}\n\n"
        "<b>Pro</b> — 500★/мес (~$10) или 4800★/год (−20%)\n"
        "20 фильтров, 200 AI-сообщений/день, instant-уведомления, price drop\n\n"
        "<b>Business</b> — 2500★/мес (~$50)\n"
        "Без лимитов, API, CRM-интеграция, приоритетная поддержка"
    )
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(
                text=_tier_label("pro", 30, 500),
                callback_data="sub:buy:pro:30",
            )],
            [InlineKeyboardButton(
                text=_tier_label("pro", 365, 4800) + " (−20%)",
                callback_data="sub:buy:pro:365",
            )],
            [InlineKeyboardButton(
                text=_tier_label("business", 30, 2500),
                callback_data="sub:buy:business:30",
            )],
        ]
    )
    await message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("sub:buy:"))
async def sub_buy(cb) -> None:
    """Выбор тарифа → pending-платёж в backend → sendInvoice (XTR)."""
    await cb.answer()
    parts = (cb.data or "").split(":")  # sub:buy:{tier}:{days}
    if len(parts) != 4:
        return
    tier, days = parts[2], parts[3]
    user_id = cb.from_user.id
    prep = await backend.prepare_payment(user_id, tier, int(days))
    if prep is None:
        await cb.message.answer("Backend недоступен, попробуйте позже.")
        return
    await cb.message.answer_invoice(
        title=prep["title"],
        description=prep["description"],
        payload=f"sub:{prep['payment_id']}",
        provider_token="",  # Telegram Stars: провайдер не нужен
        currency="XTR",
        prices=[LabeledPrice(label=prep["title"], amount=prep["amount"])],
    )


@router.pre_checkout_query()
async def pre_checkout_query(query: PreCheckoutQuery) -> None:
    """Telegram ждёт подтверждение перед списанием Stars."""
    await query.answer(ok=True)


@router.message(F.successful_payment)
async def on_successful_payment(message: Message) -> None:
    """Оплата прошла: активируем подписку через backend (ТЗ п. 8.7)."""
    sp = message.successful_payment
    if sp is None:
        return
    user_id = message.from_user.id if message.from_user else 0
    try:
        payment_id = int(sp.invoice_payload.split(":", 1)[1])
    except (IndexError, ValueError):
        logger.error("successful_payment_bad_payload: %s", sp.invoice_payload)
        return

    result = await backend.payment_success(
        payment_id=payment_id,
        user_id=user_id,
        provider_payment_id=sp.telegram_payment_charge_id,
        amount=sp.total_amount,
    )
    if result is None:
        await message.answer(
            "Оплата получена, но активация не удалась — напишите менеджеру."
        )
        return
    tier = result.get("tier", "pro")
    until = result.get("subscription_until")
    text = (
        f"✅ Оплата принята ({sp.total_amount} Stars).\n"
        f"Тариф <b>{tier}</b> активирован"
        + (f" до {str(until)[:10]}." if until else ".")
    )
    await message.answer(text)


@router.callback_query(F.data.startswith("dlg:"))
async def dlg_callback(cb, state: FSMContext) -> None:
    """Кнопки панели диалога: опции агента (с токеном хода), «Искать», сброс."""
    data = cb.data or ""
    user_id = cb.from_user.id

    if data == "dlg:reset":
        await cb.answer()
        await state.clear()
        await backend.dialog_reset(user_id)
        try:
            await cb.message.edit_text(
                "Диалог сброшен. Опишите, какую машину ищете.",
                reply_markup=build_main_menu_kb(),
            )
        except Exception:
            await cb.message.answer(
                "Диалог сброшен. Опишите, какую машину ищете.",
                reply_markup=build_main_menu_kb(),
            )
        return

    if data == "dlg:finalize":
        await cb.answer()
        result = await backend.dialog_finalize(user_id)
        if result is None:
            await cb.message.answer("Backend недоступен, попробуйте позже.")
            return
        sources = ", ".join(result.get("sources", [])) or "нет активных источников"
        kb_rows: list[list[InlineKeyboardButton]] = []
        mini = _miniapp_button("📱 Смотреть подборку")
        if mini:
            kb_rows.append([mini])
        kb_rows.append(
            [InlineKeyboardButton(text="🔄 Новый поиск", callback_data="dlg:reset")]
        )
        kb = InlineKeyboardMarkup(inline_keyboard=kb_rows)
        try:
            await cb.message.edit_text(
                "✅ <b>Поиск запущен.</b>\n"
                f"Источники: {sources}\n"
                "Пришлю уведомления о новых вариантах — их также можно "
                "смотреть в мини-приложении.",
                reply_markup=kb,
            )
        except Exception:
            await cb.message.answer(
                "✅ Поиск запущен. Источники: "
                f"{sources}. Пришлю уведомления о новых вариантах.",
                reply_markup=kb,
            )
        # Сразу показываем живые варианты фото-карточками (MVP: менеджер-опыт)
        fsm = await state.get_data()
        criteria = fsm.get("dlg_criteria") or {}
        params: dict = {}
        for src_field, query_field in (
            ("make", "make"),
            ("model", "model"),
            ("year_from", "year_from"),
            ("price_max", "price_max"),
            ("mileage_max", "mileage_max"),
        ):
            if criteria.get(src_field):
                params[query_field] = criteria[src_field]
        items = await backend.search_listings(params, limit=5)
        if items:
            await cb.message.answer(
                "🔥 <b>Вот что уже есть по вашим критериям:</b>"
            )
            await _send_car_cards(cb.message, items)
        return

    if data.startswith("dlg:opt:"):
        parts = data.split(":")
        # dlg:opt:{turn}:{idx} — клик по устаревшей кнопке игнорируем
        if len(parts) != 4:
            await cb.answer()
            return
        _, _, turn_str, idx_str = parts
        fsm_data = await state.get_data()
        if int(turn_str) != int(fsm_data.get("dlg_turn", -1)):
            await cb.answer("Эта кнопка уже устарела — отвечайте актуальной.", show_alert=False)
            return
        options = fsm_data.get("dlg_options", [])
        try:
            option = options[int(idx_str)]
        except (ValueError, IndexError):
            await cb.answer("Кнопка устарела — напишите ответ текстом.")
            return
        await cb.answer()
        await _agent_turn(cb.message, option, state, user_id=user_id)


def _miniapp_kb(path: str = "") -> InlineKeyboardMarkup | None:
    rows = []
    mini = _miniapp_button("📱 Открыть в приложении", path)
    if mini:
        rows.append([mini])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


@router.callback_query(F.data.startswith("calc:"))
async def calc_from_card(cb) -> None:
    """🚚 Доставка и растаможка прямо с фото-карточки объявления."""
    await cb.answer("Считаю…")
    try:
        listing_id = int((cb.data or "").split(":", 1)[1])
    except (ValueError, IndexError):
        return
    listing = await backend.get_listing(listing_id)
    if not listing:
        await cb.message.answer("Объявление не найдено.")
        return
    price_usd = _price_usd(listing)
    if not price_usd or not listing.get("year"):
        await cb.message.answer(
            "У объявления нет цены или года — посчитаю вручную: пришлите "
            "<code>цена $ объём год</code> (например <code>18000 2000 2022</code>)."
        )
        return
    out = await backend.calc_delivery(price_usd, int(listing.get("engine_volume") or 2000), int(listing["year"]))
    if out is None:
        await cb.message.answer("Сервис расчёта недоступен, попробуйте позже.")
        return
    await cb.message.answer(
        _format_calc_out(out),
        reply_markup=_miniapp_kb(f"/calc?price={out['price_usd']:.0f}&cc={out['engine_cc']}&year={out['year']}"),
    )


@router.message(Command("calc"))
async def cmd_calc(message: Message, command: CommandObject | None) -> None:
    """/calc 18000 2000 2022 — смета «под ключ» Корея → РФ."""
    raw_args = command.args if command else ""
    nums = [n.replace(" ", "") for n in re.findall(r"[\d][\d ]*", raw_args)]
    if len(nums) >= 2:
        price = float(nums[0])
        cc = int(nums[1]) if len(nums) >= 3 else 2000
        year = int(nums[-1])
        if 1900 <= year <= 2100 and price > 0:
            out = await backend.calc_delivery(price, cc, year)
            if out is not None:
                await message.answer(
                    _format_calc_out(out),
                    reply_markup=_miniapp_kb(f"/calc?price={price:.0f}&cc={cc}&year={year}"),
                )
                return
    await message.answer(
        "🧮 Пришлите одной строкой: <b>цена $, объём см³, год</b>\n"
        "Например: <code>18000 2000 2022</code> (объём можно опустить: <code>18000 2022</code>)",
        reply_markup=_miniapp_kb("/calc"),
    )


async def _agent_turn(
    message: Message, text: str, state: FSMContext, *, user_id: int | None = None
) -> None:
    """Один ход диалога; при недоступности LLM — кнопочный режим (п. 7.9).

    user_id берём явно: из callback-сообщения (это сообщение бота-панели)
    достать id пользователя нельзя.
    """
    if user_id is None:
        user_id = message.from_user.id if message.from_user else 0
    reply = await backend.dialog_message(user_id, text)
    if reply is None:
        from app.bot.button_flow import start_button_flow

        logger.warning("dialog_unavailable_falling_back: user_id=%s", user_id)
        await start_button_flow(message, state)
        return
    await send_dialog_reply(message, reply, state)


@router.message(F.text)
async def fallback_text(message: Message, state: FSMContext) -> None:
    """Свободный текст → AI-агент через backend (ТЗ п. 7.13)."""
    await _agent_turn(message, message.text or "", state)


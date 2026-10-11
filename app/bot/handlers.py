"""Хендлеры бота. Команды + AI-диалог через backend (этап 3, ТЗ п. 7)."""

import html
import json
import logging

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
    "/stop — отключить уведомления\n"
    "/resume — включить уведомления\n"
    "/subscribe — подписка Pro/Business (Stars)\n\n"
    "Просто напишите текстом, какую машину ищете — я помогу с подбором."
)

START_TEXT = (
    "👋 Привет, {name}!\n\n"
    "Я помогу подобрать автомобиль из Кореи, Китая, Европы, США или Японии.\n\n"
    "Опишите словами, что ищете — например: "
    "<i>«Hyundai Palisade 2022–2024, дизель, до 45к $»</i> — и я начну подбор.\n\n"
    + HELP_TEXT
)


def build_reply_kb(reply: dict) -> InlineKeyboardMarkup:
    """Кнопки под каждым ответом: опции агента + «Создать поиск» (ТЗ п. 7.4)."""
    rows = [
        [InlineKeyboardButton(text=opt, callback_data=f"dlg:opt:{i}")]
        for i, opt in enumerate(reply.get("options", []))
    ]
    if reply.get("finalize_ready"):
        rows.append(
            [InlineKeyboardButton(text="🔎 Искать", callback_data="dlg:finalize")]
        )
    rows.append([InlineKeyboardButton(text="🔄 Сбросить", callback_data="dlg:reset")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def send_dialog_reply(message: Message, reply: dict, state: FSMContext) -> None:
    """Показать ответ агента; кнопки-опции храним в FSM-data (короткий срок)."""
    await state.update_data(dlg_options=reply.get("options", []))
    criteria = reply.get("criteria") or {}
    filled = {k: v for k, v in criteria.items() if v is not None and v != []}
    text = html.escape(reply.get("text", ""))
    if filled:
        text += "\n\n<i>Критерии:</i> " + html.escape(
            json.dumps(filled, ensure_ascii=False)
        )
    await message.answer(text, reply_markup=build_reply_kb(reply))


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
    """URL Mini App, но только HTTPS: Telegram отвергает http-WebApp-кнопки
    (Bad Request прямо при отправке сообщения). Для локальной демки (http)
    возвращаем пусто — кнопки не добавляются, бот работает."""
    from app.core.config import get_settings

    url = get_settings().miniapp_url
    return url if url.startswith("https://") else ""


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
    from aiogram.types import WebAppInfo

    rows: list[list[InlineKeyboardButton]] = []
    miniapp = _miniapp_url()
    if miniapp:
        rows.append(
            [InlineKeyboardButton(
                text="📱 Фото и детали", web_app=WebAppInfo(url=miniapp)
            )]
        )
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
        from aiogram.types import WebAppInfo

        miniapp = _miniapp_url()
        rows: list[list[InlineKeyboardButton]] = []
        if miniapp:
            rows.append(
                [InlineKeyboardButton(
                    text="📱 Открыть фильтр в приложении",
                    web_app=WebAppInfo(url=miniapp),
                )]
            )
        await message.answer(
            "🔍 Фильтр найден — управление им в приложении.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
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


def build_main_menu_kb() -> InlineKeyboardMarkup:
    """Главное инлайн-меню бота (WebApp + быстрые действия)."""
    from aiogram.types import WebAppInfo

    from app.core.config import get_settings

    miniapp_url = get_settings().miniapp_url
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="🚗 AI-подбор авто", callback_data="menu:ai"
            )
        ],
        [InlineKeyboardButton(text="📱 Открыть приложение", web_app=WebAppInfo(url=miniapp_url))],
        [
            InlineKeyboardButton(text="🔔 Уведомления", callback_data="menu:notify"),
            InlineKeyboardButton(text="⭐ Подписка", callback_data="menu:subscribe"),
        ],
        [
            InlineKeyboardButton(text="🔄 Сбросить диалог", callback_data="menu:reset"),
            InlineKeyboardButton(text="❓ Помощь", callback_data="menu:help"),
        ],
    ]
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
    """Кнопки диалога: опции агента, «Искать», сброс."""
    await cb.answer()
    data = cb.data or ""
    user_id = cb.from_user.id

    if data == "dlg:reset":
        await state.clear()
        await backend.dialog_reset(user_id)
        await cb.message.answer("Диалог сброшен. Опишите, какую машину ищете.")
        return

    if data == "dlg:finalize":
        await cb.message.answer("🔎 Запускаю поиск…")
        result = await backend.dialog_finalize(user_id)
        if result is None:
            await cb.message.answer("Backend недоступен, попробуйте позже.")
        else:
            sources = ", ".join(result.get("sources", [])) or "нет активных источников"
            await cb.message.answer(
                "✅ Поиск запущен. Источники: "
                f"{sources}. Пришлю уведомления о новых вариантах."
            )
        return

    if data.startswith("dlg:opt:"):
        fsm_data = await state.get_data()
        options = fsm_data.get("dlg_options", [])
        try:
            idx = int(data.rsplit(":", 1)[1])
            option = options[idx]
        except (ValueError, IndexError):
            await cb.message.answer("Кнопка устарела — напишите ответ текстом.")
            return
        await _agent_turn(cb.message, option, state)


async def _agent_turn(message: Message, text: str, state: FSMContext) -> None:
    """Один ход диалога; при недоступности LLM — кнопочный режим (п. 7.9)."""
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


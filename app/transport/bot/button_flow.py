"""Кнопочный fallback, когда LLM недоступна (ТЗ п. 7.9).

Единая «живая» панель: каждый шаг редактирует предыдущее сообщение,
никакого вала сообщений. Кнопки обрабатываются колбэками (btn:{поле}:{индекс}).
"""

import html
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.transport.bot.client import BackendClient

router = Router(name="button_flow")


class ButtonForm(StatesGroup):
    make = State()
    model = State()
    year = State()
    budget = State()


POPULAR_MAKES = ["Hyundai", "Kia", "Genesis", "BMW", "Mercedes-Benz", "Toyota", "Lexus"]
YEAR_OPTIONS = ["2020–2022", "2022–2024", "2023–2025", "Не важно"]
BUDGET_OPTIONS = ["до 30 000 $", "30 000–45 000 $", "45 000–60 000 $", "60 000+ $"]

_STEP_TOTAL = 4  # MVP: только Корея — шаг страны убран


def _kb(field: str, options: list[str]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=o, callback_data=f"btn:{field}:{i}")]
        for i, o in enumerate(options)
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _panel_kb() -> InlineKeyboardMarkup:
    """Навигация панели: мини-апп + сброс (кнопки есть на каждом шаге)."""
    from app.transport.bot.handlers import _miniapp_button

    rows: list[list[InlineKeyboardButton]] = []
    mini = _miniapp_button("📱 Мини-приложение")
    if mini:
        rows.append([mini])
    rows.append([InlineKeyboardButton(text="🔄 Сбросить", callback_data="btn:reset")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_step(
    message: Message, state: FSMContext, header: str, kb: InlineKeyboardMarkup | None = None
) -> None:
    """Редактировать панель на месте; новым сообщением — только при первом шаге."""
    data = await state.get_data()
    panel_id = data.get("panel_msg_id")
    if panel_id:
        try:
            await message.bot.edit_message_text(
                chat_id=message.chat.id,
                message_id=panel_id,
                text=header,
                reply_markup=kb,
            )
            return
        except Exception:
            pass
    panel = await message.answer(header, reply_markup=kb)
    await state.update_data(panel_msg_id=panel.message_id)


def _parse_years(text: str) -> dict:
    nums = [int(n) for n in re.findall(r"\d{4}", text)]
    if not nums:
        return {}
    return {"year_from": nums[0], "year_to": nums[-1]}


def _parse_budget(text: str) -> dict:
    nums = [int(n.replace(" ", "")) for n in re.findall(r"\d[\d\s]*", text)]
    if "+" in text and nums:
        return {"price_min": nums[0]}
    if len(nums) >= 2:
        return {"price_min": nums[0], "price_max": nums[1]}
    if nums:
        return {"price_max": nums[0]}
    return {}


async def start_button_flow(message: Message, state: FSMContext) -> None:
    await state.set_state(ButtonForm.make)
    await _show_step(
        message,
        state,
        "🤖 AI-режим временно недоступен — соберу параметры через кнопки.\n\n"
        f"<b>Шаг 1/{_STEP_TOTAL}.</b> Выберите марку:",
        kb=_kb("make", POPULAR_MAKES),
    )


async def _finalize(message: Message, state: FSMContext, data: dict) -> None:
    from app.transport.bot.handlers import _miniapp_button, format_criteria

    backend: BackendClient = message.bot["backend"]
    user_id = message.from_user.id if message.from_user else 0
    result = await backend.dialog_finalize(user_id, criteria=data)
    await state.clear()
    if result is None:
        await _show_step(
            message,
            state,
            "Не удалось запустить поиск — backend недоступен. Попробуйте позже.",
            kb=_panel_kb(),
        )
        return
    sources = ", ".join(result.get("sources", [])) or "нет активных источников"
    rows: list[list[InlineKeyboardButton]] = []
    mini = _miniapp_button("📱 Смотреть подборку")
    if mini:
        rows.append([mini])
    kb = InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
    criteria = result.get("criteria") or data
    await _show_step(
        message,
        state,
        "🔎 <b>Поиск запущен!</b>\n\n"
        "📋 <b>Параметры:</b>\n" + format_criteria(criteria)
        + f"\n\nИсточники: {sources}\nПришлю уведомления о подходящих вариантах.",
        kb=kb,
    )


async def _apply_choice(
    cb_message: Message, state: FSMContext, field: str, choice_text: str
) -> None:
    """Применить выбор кнопки и показать следующий шаг (на той же панели)."""
    if field == "make":
        await state.update_data(make=choice_text)
        await state.set_state(ButtonForm.model)
        await _show_step(
            cb_message,
            state,
            f"✅ Марка: <b>{html.escape(choice_text)}</b>\n\n"
            f"<b>Шаг 2/{_STEP_TOTAL}.</b> Модель? (или «Пропустить»)",
        )
        return
    if field == "year":
        await state.update_data(**_parse_years(choice_text))
        await state.set_state(ButtonForm.budget)
        await _show_step(
            cb_message,
            state,
            f"✅ Годы: <b>{html.escape(choice_text)}</b>\n\n"
            f"<b>Шаг 4/{_STEP_TOTAL}.</b> Бюджет:",
            kb=_kb("budget", BUDGET_OPTIONS),
        )
        return
    if field == "budget":
        await state.update_data(**_parse_budget(choice_text))
        data = await state.get_data()
        data["country"] = "KR"  # MVP: работаем только с Кореей
        data["created_from_dialog"] = True
        await _finalize(cb_message, state, data)


@router.callback_query(F.data.startswith("btn:"))
async def btn_click(cb: CallbackQuery, state: FSMContext) -> None:
    """Кнопки шагов: марка/годы/бюджет/страна + сброс."""
    parts = (cb.data or "").split(":")
    if cb.message is None or len(parts) != 3:
        await cb.answer()
        return
    _, field, idx_str = parts
    if field == "reset":
        await cb.answer()
        await state.clear()
        await cb.message.edit_text(
            "Диалог сброшен. Опишите, какую машину ищете.", reply_markup=None
        )
        return

    catalog = {
        "make": POPULAR_MAKES,
        "year": YEAR_OPTIONS,
        "budget": BUDGET_OPTIONS,
    }.get(field)
    if catalog is None:
        await cb.answer("Неизвестная кнопка.")
        return
    try:
        choice = catalog[int(idx_str)]
    except (ValueError, IndexError):
        await cb.answer("Кнопка устарела — начните заново (/reset).")
        return
    await cb.answer()
    await _apply_choice(cb.message, state, field, choice)


@router.message(ButtonForm.make, F.text)
async def fsm_make(message: Message, state: FSMContext) -> None:
    await state.update_data(make=message.text)
    await state.set_state(ButtonForm.model)
    await _show_step(
        message,
        state,
        f"✅ Марка: <b>{html.escape(message.text or '')}</b>\n\n"
        f"<b>Шаг 2/{_STEP_TOTAL}.</b> Модель? (или «Пропустить»)",
    )


@router.message(ButtonForm.model, F.text)
async def fsm_model(message: Message, state: FSMContext) -> None:
    model = None if (message.text or "").strip().lower() == "пропустить" else message.text
    await state.update_data(model=model)
    await state.set_state(ButtonForm.year)
    shown = html.escape(model or "—")
    await _show_step(
        message,
        state,
        f"✅ Модель: <b>{shown}</b>\n\n"
        f"<b>Шаг 3/{_STEP_TOTAL}.</b> Годы выпуска:",
        kb=_kb("year", YEAR_OPTIONS),
    )


@router.message(ButtonForm.year, F.text)
async def fsm_year(message: Message, state: FSMContext) -> None:
    await state.update_data(**_parse_years(message.text or ""))
    await state.set_state(ButtonForm.budget)
    shown = html.escape(message.text or "—")
    await _show_step(
        message,
        state,
        f"✅ Годы: <b>{shown}</b>\n\n"
        f"<b>Шаг 4/{_STEP_TOTAL}.</b> Бюджет:",
        kb=_kb("budget", BUDGET_OPTIONS),
    )


@router.message(ButtonForm.budget, F.text)
async def fsm_budget(message: Message, state: FSMContext) -> None:
    await state.update_data(**_parse_budget(message.text or ""))
    data = await state.get_data()
    data["country"] = "KR"  # MVP: работаем только с Кореей
    data["created_from_dialog"] = True
    await _finalize(message, state, data)

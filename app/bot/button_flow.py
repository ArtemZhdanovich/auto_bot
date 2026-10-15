"""Кнопочный fallback, когда LLM недоступна (ТЗ п. 7.9)."""

import html
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.client import BackendClient

router = Router(name="button_flow")


class ButtonForm(StatesGroup):
    make = State()
    model = State()
    year = State()
    budget = State()
    country = State()


POPULAR_MAKES = ["Hyundai", "Kia", "Genesis", "BMW", "Mercedes-Benz", "Toyota", "Lexus"]
YEAR_OPTIONS = ["2020–2022", "2022–2024", "2023–2025", "Не важно"]
BUDGET_OPTIONS = ["до 30 000 $", "30 000–45 000 $", "45 000–60 000 $", "60 000+ $"]
COUNTRY_OPTIONS = ["Корея", "Китай", "Германия", "США", "Япония"]
COUNTRY_CODES = {"корея": "KR", "китай": "CN", "германия": "DE", "сша": "US", "япония": "JP"}


def _reply_kb(options: list[str]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=o, callback_data=f"btn:{o}")] for o in options]
    return InlineKeyboardMarkup(inline_keyboard=rows)


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
    await message.answer(
        "🤖 AI-режим временно недоступен — соберу параметры через кнопки.\n\n"
        "<b>Шаг 1/5.</b> Выберите марку:",
        reply_markup=_reply_kb(POPULAR_MAKES),
    )


async def _finalize(message: Message, state: FSMContext, data: dict) -> None:
    backend: BackendClient = message.bot["backend"]
    user_id = message.from_user.id if message.from_user else 0
    result = await backend.dialog_finalize(user_id, criteria=data)
    await state.clear()
    if result is None:
        await message.answer(
            "Не удалось запустить поиск — backend недоступен. Попробуйте позже."
        )
        return
    sources = ", ".join(result.get("sources", [])) or "нет активных источников"
    await message.answer(
        "🔎 Поиск запущен! Критерии:\n"
        f"<code>{html.escape(str(result['criteria']))}</code>\n\n"
        f"Источники: {sources}\nПришлю уведомления о подходящих вариантах.",
        reply_markup=None,
    )


@router.message(ButtonForm.make, F.text)
async def fsm_make(message: Message, state: FSMContext) -> None:
    await state.update_data(make=message.text)
    await state.set_state(ButtonForm.model)
    await message.answer(
        "<b>Шаг 2/5.</b> Модель? (или «Пропустить»)", reply_markup=None
    )


@router.message(ButtonForm.model, F.text)
async def fsm_model(message: Message, state: FSMContext) -> None:
    model = None if message.text.strip().lower() == "пропустить" else message.text
    await state.update_data(model=model)
    await state.set_state(ButtonForm.year)
    await message.answer("<b>Шаг 3/5.</b> Годы выпуска:", reply_markup=_reply_kb(YEAR_OPTIONS))


@router.message(ButtonForm.year, F.text)
async def fsm_year(message: Message, state: FSMContext) -> None:
    await state.update_data(**_parse_years(message.text))
    await state.set_state(ButtonForm.budget)
    await message.answer("<b>Шаг 4/5.</b> Бюджет:", reply_markup=_reply_kb(BUDGET_OPTIONS))


@router.message(ButtonForm.budget, F.text)
async def fsm_budget(message: Message, state: FSMContext) -> None:
    await state.update_data(**_parse_budget(message.text))
    await state.set_state(ButtonForm.country)
    await message.answer("<b>Шаг 5/5.</b> Страна:", reply_markup=_reply_kb(COUNTRY_OPTIONS))


@router.message(ButtonForm.country, F.text)
async def fsm_country(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    code = COUNTRY_CODES.get((message.text or "").strip().lower())
    if code:
        data["country"] = code
    data["created_from_dialog"] = True
    await _finalize(message, state, data)

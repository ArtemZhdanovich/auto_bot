"""Запуск бота: python -m app.bot (ТЗ п. 4.1 — FSM в Redis)."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.button_flow import router as button_flow_router
from app.bot.client import BackendClient
from app.bot.handlers import router
from app.core.config import get_settings


async def main() -> None:
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is not set — заполните .env")

    # FSM хранится в Redis: fsm:{user_id} (ТЗ п. 4.1)
    storage = RedisStorage.from_url(settings.redis_url)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=storage)
    dp["backend"] = BackendClient()  # доступ из button_flow через message.bot
    dp.include_router(router)
    dp.include_router(button_flow_router)

    await bot.delete_webhook(drop_pending_updates=True)

    # Регистрация команд и кнопки-меню с Mini App, чтобы WebApp реально
    # открывался из клиента Telegram (кнопка слева от поля ввода).
    from aiogram.types import (
        BotCommand,
        BotCommandScopeAllPrivateChats,
        MenuButtonWebApp,
        WebAppInfo,
    )

    await bot.set_my_commands(
        [
            BotCommand(command="start", description="Запуск / главное меню"),
            BotCommand(command="menu", description="Меню"),
            BotCommand(command="reset", description="Сбросить диалог подбора"),
            BotCommand(command="help", description="Помощь"),
            BotCommand(command="stop", description="Отписаться от уведомлений"),
        ],
        scope=BotCommandScopeAllPrivateChats(),
    )
    if settings.miniapp_url:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🚗 Поиск авто",
                web_app=WebAppInfo(url=settings.miniapp_url),
            )
        )

    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
